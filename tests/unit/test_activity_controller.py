import asyncio
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, Mock

from nativepeers.activity import Reader, store
from nativepeers.activity.context import begin_task, fallback_context
from nativepeers.artifacts import Artifacts
from nativepeers.compatibility import fingerprint
from nativepeers.files import atomic_json, canonical, private_directory
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.registry import Registry
from nativepeers.supervisor import Group
from nativepeers.worker_process import Worker

ROOT = Path(__file__).resolve().parents[2]
CLAUDE_STREAM = [json.loads(line) for line in (ROOT / 'tests/fixtures/activity/claude/2.1.314-stream.jsonl').read_text().splitlines()]


def events(journal, worker_id):
    return Reader(journal.root, worker_id).read(0, limit=512)['events']


def kinds(found):
    return [e['kind'] for e in found]


class ControllerCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.j = Journal(Path(self.directory.name))
        self.addCleanup(self.j.close)

    def fixture(self, provider='claude', deadline=None, task_json=None):
        group, worker, first, second, native = [new_id() for _ in range(5)]
        policy = {'initial_tasks': [first], 'parent_incarnation': new_id(), 'phases': {'1': [first], '2': [second]}}
        lease = {'provider_home': str(self.j.root.resolve()), 'spec': {'provider': provider, 'model': 'sonnet', 'max_task_turns': 8, 'wall_time_seconds': 5},
                 'binding': str(self.j.root / 'bindings/test'), 'parent_key': 'parent'}
        self.j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)', (group, 'parent', canonical(policy).decode(), 'RUNNING', '{}'))
        self.j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                          (worker, group, 'peer', provider, native, 'worker', 'sonnet', None, None, str(self.j.root), None, 'RUNNING', 1, canonical(lease).decode()))
        for task in (first, second):
            body = dict(task_json or {}, **({'deadline': deadline} if deadline else {}))
            self.j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)', (task, worker, None, None, 'QUEUED', None, None, 1, canonical(body).decode()))
        w = Worker(self.j, worker)
        w.emit = Mock()
        return w, first, second

    def terminal_of(self, worker, task_id=None):
        found = [e for e in events(self.j, worker.row['worker_id']) if e['kind'] == 'task.terminal' and (task_id is None or e['task_id'] == task_id)]
        self.assertEqual(len(found), 1, found)
        return found[0]


class TaskLifecycleTests(ControllerCase):
    async def test_a_task_is_recorded_from_its_start_to_the_controllers_terminal_outcome(self):
        w, first, _ = self.fixture()

        async def turn(text):
            self.assertIn('NativePeers assigned worker title', text)
            for event in CLAUDE_STREAM:
                w.activity.feed(event)
            return {'status': 'completed', 'text': 'Done: notes.txt now reads one/three.', 'native_turn_ids': []}

        w.claude_turn = turn
        await w.task(first, 'tidy the notes')
        found = events(self.j, w.row['worker_id'])
        self.assertEqual(kinds(found)[:2], ['task.started', 'task.state'])
        self.assertEqual(found[0]['data'], dict(origin='parent-model', client_request_id=None, input_preview='tidy the notes'))
        self.assertIn('files.changed', kinds(found))
        self.assertEqual(kinds(found).count('tool.started'), kinds(found).count('tool.completed'))
        terminal = found[-1]
        self.assertEqual(terminal['kind'], 'task.terminal')
        self.assertEqual(terminal['data']['status'], 'completed')
        self.assertEqual(terminal['data']['final_text'], 'Done: notes.txt now reads one/three.')
        row = self.j.one('SELECT state,result_artifact_id FROM tasks WHERE task_id=?', (first,))
        self.assertEqual((row['state'], terminal['data']['result_artifact_id']), ('COMPLETED', row['result_artifact_id']))
        self.assertTrue(all(e['task_id'] == first and e['generation'] == 1 and e['worker_id'] == w.row['worker_id'] for e in found))
        self.assertEqual([e['seq'] for e in found], list(range(1, len(found) + 1)))

    async def test_terminal_is_written_before_the_controller_announces_completion(self):
        w, first, _ = self.fixture()
        w.claude_turn = AsyncMock(return_value={'status': 'completed', 'text': 'ok', 'native_turn_ids': []})
        seen = []

        def emit(event, **payload):
            if event == 'task_terminal':
                seen.append(kinds(events(self.j, w.row['worker_id'])))

        w.emit = emit
        await w.task(first, 'go')
        await w.activity.flush()
        self.assertEqual(len(seen), 1)

    async def test_every_outcome_is_recorded_once_with_the_controllers_status(self):
        outcomes = {}
        for name in ('failed', 'timed_out', 'canceled', 'interrupted', 'quota'):
            w, first, _ = self.fixture(deadline=time.time() + 0.4 if name == 'timed_out' else None)
            started = asyncio.Event()
            if name == 'failed':
                w.claude_turn = AsyncMock(return_value={'status': 'failed', 'text': '', 'error': {'code': 'PROVIDER_TASK_FAILED', 'message': 'it broke'}, 'native_turn_ids': []})
            elif name == 'quota':
                w.claude_turn = AsyncMock(return_value={'status': 'failed', 'text': 'limit', 'error': {'code': 'QUOTA_EXHAUSTED', 'native_error': {'message': 'weekly limit'}}})
            elif name == 'timed_out':
                async def slow(text, started=started):
                    started.set()
                    await asyncio.sleep(30)
                w.claude_turn = slow
            elif name == 'canceled':
                interrupted = asyncio.Event()

                async def waits(text, started=started, interrupted=interrupted):
                    started.set()
                    await interrupted.wait()
                    return {'status': 'canceled', 'text': 'partial'}

                async def interrupt(interrupted=interrupted):
                    interrupted.set()
                w.claude_turn, w.interrupt = waits, interrupt
            else:
                async def crash(text):
                    w.stopping, w.shutdown_reason = True, 'SUPERVISOR_LEASE_LOST'
                    raise RuntimeError('lost')
                w.claude_turn = crash
            run = asyncio.create_task(w.task(first, 'go'))
            if name == 'canceled':
                await started.wait()
                await w.cancel_task(first)
            await run
            terminal = self.terminal_of(w, first)
            journal_state = self.j.one('SELECT state FROM tasks WHERE task_id=?', (first,))['state']
            outcomes[name] = (terminal['data']['status'], journal_state, terminal['data'].get('error', {}).get('code'), terminal['data'].get('error', {}).get('message'))
            kinds_found = kinds(events(self.j, w.row['worker_id']))
            self.assertEqual(kinds_found[0], 'task.started')
            self.assertEqual(kinds_found.count('task.terminal'), 1)
            await w.activity.close()
        self.assertEqual(outcomes['failed'], ('failed', 'FAILED', 'PROVIDER_TASK_FAILED', 'it broke'))
        self.assertEqual(outcomes['quota'], ('failed', 'FAILED', 'QUOTA_EXHAUSTED', 'weekly limit'))
        self.assertEqual(outcomes['timed_out'][:2], ('timed_out', 'TIMED_OUT'))
        self.assertEqual(outcomes['canceled'][:3], ('canceled', 'CANCELED', 'USER_CANCELED'))
        self.assertEqual(outcomes['interrupted'][:3], ('interrupted', 'INTERRUPTED', 'DELIVERY_UNKNOWN_AFTER_OWNER_LOSS'))

    async def test_a_codex_turn_the_provider_calls_interrupted_keeps_the_controllers_canceled_and_says_so(self):
        w, first, _ = self.fixture('codex')
        task = None

        async def turn(text):
            w.turn = 'turn-1'
            w.activity.feed(dict(method='turn/completed', params=dict(threadId='t', turn=dict(id='turn-1', items=[], status='interrupted'))))
            return {'status': 'canceled', 'text': '', 'native_turn_ids': ['turn-1']}

        w.codex_turn = turn
        await w.task(first, 'go')
        terminal = self.terminal_of(w, first)
        self.assertEqual((terminal['data']['status'], terminal['data']['native_status']), ('canceled', 'interrupted'))
        self.assertEqual(terminal['source']['event'], 'turn/completed')

    async def test_antigravity_and_grok_produce_the_generic_lifecycle(self):
        for provider, usage in (('antigravity', {'input_tokens': 7, 'output_tokens': 3}), ('grok', {'input_tokens': 9, 'output_tokens': 4, 'cache_read_input_tokens': 2, 'reasoning_tokens': 1})):
            with self.subTest(provider=provider):
                w, first, _ = self.fixture(provider)
                setattr(w, provider + '_turn', AsyncMock(return_value={'status': 'completed', 'text': 'native answer', 'native_turn_ids': [], 'usage': usage}))
                await w.task(first, 'go')
                found = events(self.j, w.row['worker_id'])
                self.assertEqual(kinds(found), ['task.started', 'task.state', 'message.completed', 'usage', 'task.terminal'])
                self.assertEqual(found[2]['data'], dict(text='native answer', is_final_candidate=True))
                self.assertFalse(found[3]['data']['cumulative'])
                self.assertEqual(found[3]['data']['input_tokens'], usage['input_tokens'])
                self.assertEqual(found[-1]['data']['status'], 'completed')
                self.assertTrue(all(e['source']['provider'] == provider for e in found))

    async def test_a_controller_stopped_mid_task_closes_the_log_with_one_interrupted_terminal(self):
        w, first, _ = self.fixture()
        w.activity.begin(first, 1)
        w.activity.emit('tool.started', dict(kind='exec', title='sleep', input={}), item='c1')
        w.shutdown_reason = 'SUPERVISOR_LEASE_LOST'
        await w.close()
        await w.close()
        found = events(self.j, w.row['worker_id'])
        self.assertEqual(kinds(found), ['task.started', 'task.state', 'tool.started', 'tool.completed', 'task.terminal'])
        self.assertEqual((found[3]['data']['status'], found[4]['data']['status'], found[4]['data']['error']['code']), ('interrupted', 'interrupted', 'SUPERVISOR_LEASE_LOST'))

    async def test_a_resumed_controller_continues_the_sequence_in_a_new_generation(self):
        w, first, second = self.fixture()
        w.claude_turn = AsyncMock(return_value={'status': 'completed', 'text': 'one', 'native_turn_ids': []})
        await w.task(first, 'go')
        await w.activity.close()
        before = events(self.j, w.row['worker_id'])
        self.j.db.execute("UPDATE workers SET generation=2,state='RESERVED' WHERE worker_id=?", (w.row['worker_id'],))
        self.j.db.execute("UPDATE tasks SET generation=2 WHERE task_id=?", (second,))
        resumed = Worker(self.j, w.row['worker_id'])
        resumed.emit = Mock()
        resumed.claude_turn = AsyncMock(return_value={'status': 'completed', 'text': 'two', 'native_turn_ids': []})
        await resumed.task(second, 'again')
        await resumed.activity.close()
        found = events(self.j, w.row['worker_id'])
        self.assertEqual([e['seq'] for e in found], list(range(1, len(found) + 1)))
        self.assertGreater(len(found), len(before))
        self.assertEqual([(e['generation'], e['task_id']) for e in found if e['kind'] == 'task.terminal'], [(1, first), (2, second)])
        rows = self.j.all('SELECT generation,task_id,terminal_seq FROM activity_index WHERE worker_id=? ORDER BY first_seq', (w.row['worker_id'],))
        self.assertEqual([(r['generation'], r['task_id']) for r in rows], [(1, first), (2, second)])
        self.assertTrue(all(r['terminal_seq'] for r in rows))

    async def test_a_task_that_is_not_claimed_records_nothing(self):
        w, first, _ = self.fixture()
        self.j.db.execute("UPDATE tasks SET state='CANCELED' WHERE task_id=?", (first,))
        w.claude_turn = AsyncMock()
        await w.task(first, 'late')
        w.claude_turn.assert_not_awaited()
        self.assertEqual(events(self.j, w.row['worker_id']), [])


class TaskContextTests(ControllerCase):
    async def test_origin_and_request_ids_come_from_the_journal(self):
        w, first, second = self.fixture(task_json={'initial': True})
        self.j.db.execute('INSERT INTO spawn_requests VALUES(?,?,?,?)', ('parent', 'spawn-req-1', 'digest', w.row['group_id']))
        self.j.db.execute('INSERT INTO resume_requests VALUES(?,?,?,?,?)', ('parent', 'resume-req-1', 'digest', second, '{}'))
        w.claude_turn = AsyncMock(return_value={'status': 'completed', 'text': 'ok', 'native_turn_ids': []})
        await w.task(first, 'initial brief')
        self.j.db.execute("UPDATE tasks SET task_json=? WHERE task_id=?", (canonical({'initial': False, 'origin': 'human'}).decode(), second))
        await w.task(second, 'follow-up')
        started = [e['data'] for e in events(self.j, w.row['worker_id']) if e['kind'] == 'task.started']
        self.assertEqual([(d['origin'], d['client_request_id']) for d in started], [('parent-model', 'spawn-req-1'), ('human', 'resume-req-1')])

    async def test_a_fallback_attempt_records_where_it_came_from_and_why(self):
        w, first, _ = self.fixture('codex')
        old = new_id()
        lease = {'spec': {'provider': 'claude'}, 'binding': str(self.j.root / 'bindings/old'), 'parent_key': 'parent'}
        self.j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                          (old, w.row['group_id'], 'peer', 'claude', new_id(), 'worker', 'opus', 'claude-opus-5-5', None, str(self.j.root), None, 'RELEASED_RESUMABLE', 1, canonical(lease).decode()))
        artifact = Artifacts(self.j).put(canonical({'error': {'code': 'QUOTA_EXHAUSTED'}}), 'owner', ['parent'], media_type='application/json')
        self.j.db.execute('INSERT INTO task_attempts VALUES(?,?,?,?,?)', (first, 0, old, 'FAILED', artifact))
        self.j.db.execute("UPDATE tasks SET task_json=? WHERE task_id=?", (canonical({'attempt_index': 1}).decode(), first))
        row = self.j.one('SELECT * FROM workers WHERE worker_id=?', (w.row['worker_id'],))
        self.assertEqual(fallback_context(self.j, row, first, 1),
                         {'from': dict(provider='claude', model='claude-opus-5-5'), 'to': dict(provider='codex', model='sonnet'), 'reason': 'QUOTA_EXHAUSTED'})
        w.codex_turn = AsyncMock(return_value={'status': 'completed', 'text': 'ok', 'native_turn_ids': []})
        await w.task(first, 'retry elsewhere')
        found = events(self.j, w.row['worker_id'])
        self.assertEqual(kinds(found)[:3], ['task.started', 'task.state', 'fallback.started'])
        self.assertEqual(found[0]['data']['origin'], 'fallback')
        self.assertEqual(found[2]['data']['reason'], 'QUOTA_EXHAUSTED')

    async def test_a_failing_journal_lookup_never_reaches_the_task(self):
        w, first, _ = self.fixture()
        broken = Mock()
        broken.one.side_effect = RuntimeError('database is locked')
        begin_task(w.activity, broken, w.row, first, {}, 'text')
        w.activity.terminal('completed')
        await w.activity.flush()
        self.assertEqual(kinds(events(self.j, w.row['worker_id']))[0], 'task.started')


class RealControllerTests(unittest.IsolatedAsyncioTestCase):
    async def run_worker(self, provider, script=None):
        with tempfile.TemporaryDirectory() as temporary:
            j = Journal(Path(temporary))
            group_id, parent_id = new_id(), new_id()
            parent = Registry(j).register('codex', Path(temporary), parent_id, 'fake parent', identity(os.getpid()).json(), temporary, {'kind': 'fake'})
            policy = dict(original_request={}, workers=[], initial_tasks=[], completion_mode='wait', followup_grace_seconds=0, wall_time_seconds=10, depth=1,
                          allow_sibling_messages=False, survive_parent=False, created_at=time.time(), parent_owner=parent['owner'], parent_incarnation=parent['incarnation_id'])
            j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)', (group_id, parent['handle'], '{}', 'RESERVED', '{}'))
            j.db.execute('INSERT INTO completion_routes VALUES(?,?,?,?,?,?,?,?,?)', (group_id, 1, parent['handle'], 'wait', 'UNCLAIMED', None, None, new_id(), 1))
            private_directory(j.root / 'groups' / group_id)
            executable = Path(temporary) / ('fake_' + provider + '.py')
            executable.write_text(script or (ROOT / 'tests/fixtures' / executable.name).read_text())
            executable.chmod(0o755)
            worker_id, task_id = new_id(), new_id()
            policy['workers'].append(worker_id)
            policy['initial_tasks'].append(task_id)
            spec = dict(provider=provider, title='fake', task='nonce fixture', permission_profile='analysis', model='fake-claude' if provider == 'claude' else 'gpt-6.1-sol',
                        reasoning_effort='xhigh', cwd=temporary, wall_time_seconds=5, max_task_turns=2)
            atomic_json(j.root / 'compat/runtime.json', {'providers': {provider: fingerprint(executable)}})
            lease = dict(spec=spec, grants={'worker_id': worker_id, 'group_id': group_id}, binding=str(j.root / 'bindings' / new_id()), parent_key=parent['handle'],
                         expires_at=time.time() + 30, executable=str(executable))
            j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         (worker_id, group_id, None, provider, new_id() if provider == 'claude' else None, 'fake', spec['model'], None, 'xhigh', temporary, None, 'RESERVED', 1, canonical(lease).decode()))
            j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)', (task_id, worker_id, None, None, 'QUEUED', None, None, 1, canonical({'text': 'nonce fixture', 'initial': True}).decode()))
            j.db.execute('UPDATE groups SET policy_json=? WHERE group_id=?', (canonical(policy).decode(), group_id))
            previous = os.environ.get('NPEER_HOME')
            os.environ['NPEER_HOME'] = temporary
            group = Group(j, group_id)
            try:
                await group.initialize()
                await asyncio.wait_for(group.monitor(), 12)
            finally:
                await group.close()
                if previous is None:
                    os.environ.pop('NPEER_HOME', None)
                else:
                    os.environ['NPEER_HOME'] = previous
            row = j.one('SELECT state,result_artifact_id FROM tasks WHERE task_id=?', (task_id,))
            return events(j, worker_id), row, fingerprint(executable)['sha256'], j.all('SELECT * FROM activity_index'), store.active_path(j.root, worker_id).stat().st_mode & 0o777, task_id

    async def test_a_real_codex_controller_writes_its_log(self):
        found, row, sha, index, mode, task_id = await self.run_worker('codex')
        self.assertEqual(row['state'], 'COMPLETED')
        self.assertEqual(kinds(found), ['task.started', 'task.state', 'message.completed', 'task.terminal'])
        self.assertTrue(found[2]['data']['text'].startswith('fixture result') and found[2]['data']['is_final_candidate'])
        self.assertEqual((found[-1]['data']['status'], found[-1]['data']['result_artifact_id'], found[-1]['data']['native_status']), ('completed', row['result_artifact_id'], 'completed'))
        self.assertEqual(found[2]['source'], dict(provider='codex', runtime_sha256=sha, event='item/completed'))
        self.assertTrue(found[2]['native_turn'])
        self.assertEqual([(i['task_id'], i['first_seq'], i['last_seq'], i['terminal_seq']) for i in index], [(task_id, 1, 4, 4)])
        self.assertEqual(mode, 0o600)

    async def test_a_synthetic_claude_error_message_is_not_recorded_as_assistant_text(self):
        script = ('''#!/usr/bin/env python3
import json,sys
a=sys.argv[1:];n=a[a.index("--session-id")+1] if "--session-id" in a else a[a.index("--resume")+1]
sys.stdin.read()
print(json.dumps({"type":"system","subtype":"init","session_id":n,"model":"fake-claude","tools":["Read"]}),flush=True)
print(json.dumps({"type":"assistant","isApiErrorMessage":True,"session_id":n,"message":{"model":"<synthetic>","content":[{"type":"text","text":"You have hit your limit"}]}}),flush=True)
print(json.dumps({"type":"result","subtype":"success","is_error":True,"session_id":n,"usage":{"input_tokens":0,"output_tokens":0}}),flush=True)
''')
        found, row, *_ = await self.run_worker('claude', script)
        self.assertEqual(row['state'], 'FAILED')
        self.assertEqual(kinds(found), ['task.started', 'task.state', 'task.terminal'])
        self.assertEqual(found[-1]['data']['status'], 'failed')
        self.assertIn('error', found[-1]['data'])

    async def test_a_real_claude_controller_writes_its_log(self):
        found, row, sha, index, mode, task_id = await self.run_worker('claude')
        self.assertEqual(row['state'], 'COMPLETED')
        self.assertEqual(kinds(found), ['task.started', 'task.state', 'message.completed', 'task.terminal'])
        self.assertEqual(found[2]['data']['text'], 'intermediate')
        self.assertTrue(found[-1]['data']['final_text'].startswith('fixture result'))
        self.assertEqual((found[-1]['data']['status'], found[-1]['data']['result_artifact_id'], found[-1]['data']['native_status']), ('completed', row['result_artifact_id'], 'success'))
        self.assertEqual(found[-1]['source'], dict(provider='claude', runtime_sha256=sha, event='result'))
        self.assertEqual([(i['first_seq'], i['terminal_seq']) for i in index], [(1, 4)])


if __name__ == '__main__':
    unittest.main()
