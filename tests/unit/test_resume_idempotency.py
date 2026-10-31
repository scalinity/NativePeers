"""Resume retries recover durable task handles without launching native providers."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from nativepeers.caller import Caller
from nativepeers.errors import PeerError
from nativepeers.files import atomic_json, canonical, sha256
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.recovery import resume_worker
from nativepeers.recovery import assert_released, resume_command, reconcile_owned
from nativepeers.delegation import Delegation
from nativepeers.registry import Registry


class ResumeIdempotencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.journal = Journal(self.root / 'state')
        self.addCleanup(lambda: self.journal.close())
        self.caller = Caller('peer', 'fixture-parent', new_id())
        self.parent = {'owner': identity(os.getpid()).json(), 'incarnation_id': self.caller.incarnation,
                       'policy': {'profiles': ['analysis']}}
        self.released = self.enterContext(patch('nativepeers.recovery.assert_released'))
        self.verify = self.enterContext(patch('nativepeers.recovery.Registry.verify', return_value=self.parent))
        self.real_start=Delegation.ensure_supervisor
        self.start = self.enterContext(patch('nativepeers.recovery.Delegation.ensure_supervisor', new_callable=AsyncMock))
        self.control = self.enterContext(patch('nativepeers.recovery.group_call', new_callable=AsyncMock))
        self.enterContext(patch('asyncio.create_subprocess_exec', side_effect=AssertionError('Native launch forbidden')))
        self.worker = self.add_worker()

    def add_worker(self, parent=None):
        group, worker, native = new_id(), new_id(), new_id()
        policy = {'initial_tasks': [], 'completion_mode': 'wait'}
        lease = {'provider_home': str(self.root), 'expires_at': time.time() + 60,
                 'spec': {'model': 'claude-fixture', 'permission_profile': 'analysis',
                          'wall_time_seconds': 60, 'max_task_turns': 2}, 'grants': {'depth': 1}}
        self.journal.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',
                                (group, parent or self.caller.key, canonical(policy).decode(), 'COMPLETED', '{}'))
        self.journal.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                (worker, group, None, 'claude', native, 'offline fixture', 'claude-fixture',
                                 'claude-fixture', None, str(self.root), None, 'RELEASED_RESUMABLE', 1,
                                 canonical(lease).decode()))
        return worker

    async def resume(self, request_id='resume-fixture', **overrides):
        values = {'worker_id': self.worker, 'text': 'offline followup', 'completion_mode': 'wait',
                  'client_request_id': request_id}
        values.update(overrides)
        return await resume_worker(self.journal, self.caller, **values)

    async def test_resume_retains_explicit_limits_until_an_operator_sets_ceilings(self):
        row=self.journal.one('SELECT * FROM workers WHERE worker_id=?',(self.worker,))
        lease=json.loads(row['lease_json']);lease['spec'].update(wall_time_seconds=7200,max_task_turns=40,native_max_turns=200)
        self.journal.db.execute('UPDATE workers SET lease_json=? WHERE worker_id=?',(canonical(lease).decode(),self.worker))
        await self.resume()
        spec=json.loads(self.journal.one('SELECT lease_json FROM workers WHERE worker_id=?',(self.worker,))['lease_json'])['spec']
        self.assertEqual((spec['wall_time_seconds'],spec['max_task_turns'],spec['native_max_turns']),(7200,40,200))
        other=self.add_worker();row=self.journal.one('SELECT * FROM workers WHERE worker_id=?',(other,))
        lease=json.loads(row['lease_json']);lease['spec'].update(wall_time_seconds=7200,max_task_turns=40,native_max_turns=200)
        self.journal.db.execute('UPDATE workers SET lease_json=? WHERE worker_id=?',(canonical(lease).decode(),other))
        atomic_json(self.journal.root/'policy.json',{'task_wall_seconds':42,'max_task_turns':3,'group_wall_seconds':60,'native_max_turns':100})
        await self.resume('limited-resume',worker_id=other)
        spec=json.loads(self.journal.one('SELECT lease_json FROM workers WHERE worker_id=?',(other,))['lease_json'])['spec']
        self.assertEqual((spec['wall_time_seconds'],spec['max_task_turns'],spec['native_max_turns']),(42,3,100))
        policy=json.loads(self.journal.one('SELECT policy_json FROM groups WHERE group_id=?',(row['group_id'],))['policy_json'])
        self.assertEqual(policy['group_wall_seconds'],60)

    async def test_old_lease_resume_uses_the_native_default_without_reducing_logical_inputs(self):
        atomic_json(self.journal.root/'policy.json',{'native_max_turns_default':256})
        await self.resume()
        spec=json.loads(self.journal.one('SELECT lease_json FROM workers WHERE worker_id=?',(self.worker,))['lease_json'])['spec']
        self.assertEqual(spec['max_task_turns'],2)
        self.assertEqual(spec['native_max_turns'],256)

    def snapshot(self):
        return {table: self.journal.all('SELECT * FROM ' + table + ' ORDER BY rowid')
                for table in ('workers', 'groups', 'tasks', 'completion_routes', 'resume_requests')}

    async def test_exact_retry_precedes_mutable_checks_and_returns_same_task(self):
        first = await self.resume()
        before = self.snapshot()
        self.released.side_effect = AssertionError('Retry must not check release')
        self.verify.side_effect = AssertionError('Retry must not recheck live incarnation')
        with patch('nativepeers.policy.load', side_effect=AssertionError('Retry must not load changed policy')):
            retry = await self.resume()
        self.assertEqual(retry, first)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(before['tasks']), 1)
        self.assertEqual(len(before['completion_routes']), 1)
        self.start.assert_awaited_once()
        self.control.assert_not_awaited()
        recorded = before['resume_requests'][0]
        self.assertEqual(recorded['payload_sha256'], sha256({
            'worker_id': self.worker, 'text': 'offline followup', 'completion_mode': 'wait'}))

    async def test_uncertain_control_response_is_not_submitted_again_after_reopen(self):
        row = self.journal.one('SELECT * FROM workers WHERE worker_id=?', (self.worker,))
        atomic_json(self.journal.root / 'groups' / row['group_id'] / 'owner.json',
                    {'owner': self.parent['owner'], 'generation': 7})
        self.control.side_effect = TimeoutError('Response lost after native admission')
        with self.assertRaises(TimeoutError):
            await self.resume()
        request = self.journal.one('SELECT * FROM resume_requests')
        original = json.loads(request['response_json'])
        self.journal.db.execute("UPDATE tasks SET state='RUNNING',native_turn_id='uncertain-native-turn' WHERE task_id=?",
                                (original['task_id'],))
        self.journal.close(); self.journal = Journal(self.root / 'state')
        before = self.snapshot()
        self.released.side_effect = AssertionError('Retry must not check release')
        retry = await self.resume()
        self.assertEqual(retry, dict(original, state='RUNNING'))
        self.assertEqual(self.snapshot(), before)
        self.control.assert_awaited_once()
        self.start.assert_not_awaited()

    async def test_completed_and_interrupted_tasks_remain_the_original_request(self):
        first = await self.resume(completion_mode=None)
        for state in ('COMPLETED', 'INTERRUPTED', 'FAILED', 'CANCELED', 'TIMED_OUT'):
            with self.subTest(state=state):
                self.journal.db.execute('UPDATE tasks SET state=?,native_turn_id=?,result_artifact_id=?,terminal_reason=? WHERE task_id=?',
                                        (state, 'retained-turn', 'retained-result', 'retained-reason', first['task_id']))
                # No mutable lease, policy, release state or incarnation is needed to read a retry.
                self.journal.db.execute("UPDATE workers SET state='RELEASED_RESUMABLE',generation=9,lease_json='invalid json' WHERE worker_id=?",
                                        (self.worker,))
                self.journal.db.execute("UPDATE groups SET policy_json='invalid json'")
                before = self.snapshot()
                retry = await self.resume(completion_mode=None)
                self.assertEqual(retry, dict(first, state=state))
                self.assertEqual(self.snapshot(), before)
        self.start.assert_awaited_once()

    async def test_start_failure_after_commit_returns_reserved_task_without_relaunch(self):
        self.start.side_effect = RuntimeError('Controller startup outcome unknown')
        with self.assertRaises(RuntimeError):
            await self.resume()
        request = self.journal.one('SELECT * FROM resume_requests')
        self.journal.close(); self.journal = Journal(self.root / 'state')
        retry = await self.resume()
        self.assertEqual(retry, json.loads(request['response_json']))
        self.assertEqual(self.journal.one('SELECT COUNT(*) AS n FROM tasks')['n'], 1)
        self.assertEqual(self.journal.one('SELECT state FROM workers')['state'],'RESERVED')
        self.start.assert_awaited_once()

    async def test_definite_process_creation_failure_releases_reserved_resume_and_retains_retry(self):
        row=self.journal.one('SELECT * FROM workers WHERE worker_id=?',(self.worker,))
        # Keep a real prior-generation manifest, as normal released histories do.
        owner=dict(self.parent['owner'],pid=99999999,pgid=99999999)
        atomic_json(self.journal.root/'workers'/(self.worker+'.json'),
                    {'worker_id':self.worker,'generation':1,'native_id':row['native_id'],'owner':owner,'native_process':None,'cleanup':{'state':'CLOSED','output_eof':True}})
        atomic_json(self.journal.root/'groups'/row['group_id']/'owner.json',{'owner':owner,'generation':1})
        self.journal.db.execute("UPDATE workers SET provider='codex' WHERE worker_id=?",(self.worker,))
        self.released.side_effect=assert_released
        async def launch(group_id):return await self.real_start(Delegation(self.journal,Registry(self.journal)),group_id)
        self.start.side_effect=launch
        with patch('nativepeers.delegation.subprocess.Popen',side_effect=FileNotFoundError('fixture executable missing')) as popen:
            with self.assertRaises(PeerError) as error:await self.resume()
            self.assertEqual(error.exception.code,'SUPERVISOR_LOST')
            task_id=error.exception.details['task_id']
            retry=await self.resume()
            self.assertEqual((retry['task_id'],retry['state']),(task_id,'FAILED'))
            popen.assert_called_once()
        self.assertEqual(self.journal.one('SELECT state,generation FROM workers'),{'state':'RELEASED_RESUMABLE','generation':2})
        self.assertEqual(self.journal.one("SELECT COUNT(*) n FROM workers WHERE state NOT IN ('RELEASED_RESUMABLE','FAILED','EXITED')")['n'],0)
        self.assertEqual(reconcile_owned(self.journal,self.caller,row['group_id']),[])
        manual=resume_command(self.journal,self.caller,self.worker)
        self.assertEqual(manual['native_id'],row['native_id'])
        self.start.side_effect=None
        fresh=await self.resume('new-operation')
        self.assertNotEqual(fresh['task_id'],task_id)
        self.assertEqual(self.journal.one('SELECT generation FROM workers')['generation'],3)
        self.assertEqual((await self.resume())['task_id'],task_id)

    async def test_reusing_id_with_changed_worker_text_or_completion_rejects(self):
        other = self.add_worker()
        await self.resume()
        self.released.side_effect = AssertionError('Conflict must precede mutable checks')
        for changed in ({'text': 'different followup'}, {'completion_mode': 'push'},
                        {'completion_mode': None}, {'worker_id': other}):
            with self.subTest(changed=changed):
                before = self.snapshot()
                with self.assertRaises(PeerError) as error:
                    await self.resume(**changed)
                self.assertEqual(error.exception.code, 'IDEMPOTENCY_CONFLICT')
                self.assertEqual(self.snapshot(), before)
        self.start.assert_awaited_once()

    async def test_concurrent_exact_retries_reserve_one_generation_and_one_dispatch(self):
        admitted = asyncio.Event()
        release = asyncio.Event()

        async def start(group_id):
            admitted.set()
            await release.wait()

        self.start.side_effect = start
        first = asyncio.create_task(self.resume())
        try:
            await asyncio.wait_for(admitted.wait(), 2)
            retries = [asyncio.create_task(self.resume()) for _ in range(4)]
            release.set()
            responses = await asyncio.wait_for(asyncio.gather(first, *retries), 2)
        finally:
            release.set()
            if not first.done():
                first.cancel()
                await asyncio.gather(first, return_exceptions=True)
        self.assertTrue(all(response == responses[0] for response in responses))
        self.assertEqual(self.journal.one('SELECT generation FROM workers')['generation'], 2)
        self.assertEqual(self.journal.one('SELECT COUNT(*) AS n FROM tasks')['n'], 1)
        self.assertEqual(self.journal.one('SELECT COUNT(*) AS n FROM completion_routes')['n'], 1)
        self.start.assert_awaited_once()

    async def test_failed_transaction_rolls_back_request_task_phase_and_generation(self):
        before = self.snapshot()
        self.journal.db.execute("CREATE TRIGGER reject_resume BEFORE INSERT ON resume_requests BEGIN SELECT RAISE(ABORT,'offline rollback fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            await self.resume()
        self.assertEqual(self.snapshot(), before)
        self.start.assert_not_awaited()
        self.journal.db.execute('DROP TRIGGER reject_resume')
        first = await self.resume()
        self.assertEqual(first['client_request_id'], 'resume-fixture')
        self.assertEqual(self.journal.one('SELECT COUNT(*) AS n FROM tasks')['n'], 1)

    async def test_transaction_rechecks_caller_id_shared_by_different_group_locks(self):
        from nativepeers.recovery import _resume_request
        await self.resume()
        other = self.add_worker()
        before = self.snapshot()
        calls = 0

        def delayed_lookup(*args):
            nonlocal calls
            calls += 1
            return None if calls == 1 else _resume_request(*args)

        # Simulate the other group committing between the lock lookup and transaction.
        with patch('nativepeers.recovery._resume_request', side_effect=delayed_lookup):
            with self.assertRaises(PeerError) as error:
                await self.resume(worker_id=other)
        self.assertEqual(error.exception.code, 'IDEMPOTENCY_CONFLICT')
        self.assertEqual(calls, 2)
        self.assertEqual(self.snapshot(), before)
        self.start.assert_awaited_once()

    async def test_ids_are_scoped_to_parent_and_other_parents_cannot_read_retries(self):
        first = await self.resume()
        stranger = Caller('peer', 'other-parent', new_id())
        with self.assertRaises(PeerError) as error:
            await resume_worker(self.journal, stranger, self.worker, 'offline followup', 'wait', 'resume-fixture')
        self.assertEqual(error.exception.code, 'APPROVAL_BLOCKED')
        other = self.add_worker(parent=stranger.key)
        second = await resume_worker(self.journal, stranger, other, 'offline followup', 'wait', 'resume-fixture')
        self.assertNotEqual(first['task_id'], second['task_id'])
        self.assertEqual(self.journal.one('SELECT COUNT(*) AS n FROM resume_requests')['n'], 2)

    async def test_omitted_ids_generate_distinct_requests_and_explicit_ids_are_validated(self):
        first = await self.resume(None)
        self.journal.db.execute("UPDATE workers SET state='RELEASED_RESUMABLE' WHERE worker_id=?", (self.worker,))
        second = await self.resume(None)
        self.assertNotEqual(first['task_id'], second['task_id'])
        self.assertNotEqual(first['client_request_id'], second['client_request_id'])
        before = self.snapshot()
        for invalid in ('', 1, {}, 'invalid\x00id'):
            with self.subTest(invalid=invalid), self.assertRaises(PeerError) as error:
                await self.resume(invalid)
            self.assertEqual(error.exception.code, 'PROTOCOL_ERROR')
        self.assertEqual(self.snapshot(), before)

    async def test_existing_v1_journal_gains_resume_table_without_changing_rows(self):
        before = self.journal.all('SELECT * FROM workers')
        self.journal.db.execute('DROP TABLE resume_requests')
        self.journal.close(); self.journal = Journal(self.root / 'state')
        self.assertEqual(self.journal.db.execute('PRAGMA user_version').fetchone()[0], 1)
        self.assertEqual(self.journal.all('SELECT * FROM workers'), before)
        first = await self.resume()
        self.assertEqual(self.journal.one('SELECT task_id FROM resume_requests')['task_id'], first['task_id'])

class ResumeCliTests(unittest.IsolatedAsyncioTestCase):
    async def test_cli_forwards_the_explicit_request_id_unchanged(self):
        from nativepeers.cli import dispatch,parser
        from unittest.mock import AsyncMock,patch
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);task=root/'task.txt';task.write_text('followup')
            journal=Journal(root/'state')
            try:
                args=parser().parse_args(['resume-worker','worker','--task-file',str(task),'--completion-mode','wait','--client-request-id','retry-id'])
                caller=Caller('peer','parent','incarnation')
                with patch('nativepeers.cli.bind_caller',return_value=caller),patch('nativepeers.recovery.resume_worker',new_callable=AsyncMock) as resume:
                    resume.return_value={'task_id':'same-task','client_request_id':'retry-id'}
                    result=await dispatch(args,journal)
                    resume.assert_awaited_once_with(journal,caller,'worker','followup','wait','retry-id')
                self.assertEqual(result['client_request_id'],'retry-id')
            finally:journal.close()
