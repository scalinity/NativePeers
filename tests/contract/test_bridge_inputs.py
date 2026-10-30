"""Bridge input, interrupt and cancel through a real group control socket (CLAUDE_LINEAGE_MOD §6.5): dispositions, provenance and owners."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from nativepeers import mirror
from nativepeers.bridge_backend import NativeBackend, STEER_RUNTIMES
from nativepeers.errors import PeerError
from nativepeers.files import atomic_json, canonical
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.registry import Registry
from nativepeers.supervisor import Group

SESSION = new_id()


class BridgeInputTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve();self.j = Journal(self.root / 'state');self.addCleanup(self.j.close)
        self.other = Journal(self.j.root);self.addCleanup(self.other.close)
        registry = Registry(self.j)
        self.parent = registry.register('claude', self.root, SESSION, 'bridge parent', identity(os.getpid()).json(), str(self.root), {'kind': 'claude-inbox'},
                                        policy={'profiles': ['analysis'], 'max_depth': 1, 'max_concurrent_workers': 4})
        self.gid = new_id();self.rows = {};tasks = {}
        self.j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)', (self.gid, self.parent['handle'], '{}', 'RUNNING', '{}'))
        for name, provider, state, task_state in (('live', 'claude', 'RUNNING', 'RUNNING'), ('grace', 'claude', 'IDLE_GRACE', 'COMPLETED'),
                                                  ('released', 'claude', 'RELEASED_RESUMABLE', 'COMPLETED'), ('steering', 'codex', 'RUNNING', 'RUNNING')):
            worker, task = new_id(), new_id();tasks[name] = task
            process = subprocess.Popen(['/bin/sleep', '60']);self.addCleanup(lambda p=process: (p.terminate(), p.wait(timeout=5)))
            peer = registry.register(provider, self.root / name, new_id(), name, identity(process.pid).json(), str(self.root),
                                     {'kind': 'owned-worker', 'group_id': self.gid, 'worker_id': worker}, owner_kind='owned-worker-controller')
            lease = {'provider_home': str(self.root), 'spec': {'model': 'fixture', 'permission_profile': 'analysis', 'max_task_turns': 4, 'wall_time_seconds': 60}, 'grants': {'depth': 1}}
            self.j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (worker, self.gid, peer['handle'], provider, peer['peer_key']['native_session_id'], name,
                                                                                         'fixture', 'fixture', None, str(self.root), None, state, 1, canonical(lease).decode()))
            self.j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)', (task, worker, None, None, task_state, None, None, 1, canonical({'text': 'brief', 'initial': True}).decode()))
            self.rows[name] = dict(worker_id=worker, task_id=task)
        policy = dict(initial_tasks=list(tasks.values()), phases={'1': list(tasks.values())}, workers=[r['worker_id'] for r in self.rows.values()], completion_mode='wait',
                      parent_owner=self.parent['owner'], parent_incarnation=self.parent['incarnation_id'], created_at=time.time(), wall_time_seconds=60)
        self.j.db.execute('UPDATE groups SET policy_json=? WHERE group_id=?', (canonical(policy).decode(), self.gid))
        self.j.db.execute('INSERT INTO completion_routes VALUES(?,?,?,?,?,?,?,?,?)', (self.gid, 1, self.parent['handle'], 'wait', 'UNCLAIMED', None, None, new_id(), 1))
        with self.j.transaction() as db:
            for name in ('live', 'released', 'steering'):mirror.grant(db, self.rows[name]['worker_id'], SESSION, 'agent-' + name, self.parent['handle'], display_only=False)
        self.group = Group(self.j, self.gid)
        self.group.children = {self.rows[n]['worker_id']: {'row': {'generation': 1}} for n in ('live', 'steering')}
        self.server = await asyncio.start_unix_server(self.group.connection, path=str(self.group.endpoint));os.chmod(self.group.endpoint, 0o600)
        atomic_json(self.j.root / 'groups' / self.gid / 'owner.json', {'owner': self.group.owner, 'generation': 1, 'endpoint': str(self.group.endpoint), 'token': self.group.token})
        self.frames = []
        async def send(worker, frame):
            self.frames.append((worker, frame))
            if frame['operation'] == 'steer':
                self.group.steering[frame['request_id']].set_result(dict(steered=True))
        self.enterContext(patch.object(self.group, 'send_worker', side_effect=send))
        self.enterContext(patch.object(self.group, 'start_worker', new_callable=AsyncMock))
        self.released = self.enterContext(patch('nativepeers.recovery.assert_released'))
        self.backend = NativeBackend(self.other, self.parent, SESSION)
        self.addAsyncCleanup(self.stop)

    async def stop(self):
        self.server.close();await self.server.wait_closed();self.group.endpoint.unlink(missing_ok=True)

    def body(self, request_id, **extra):
        return dict(dict(client_request_id=request_id, text='please also check the tests', origin='human-claude-view', mode='queue', delivery_owner='mirror'), **extra)

    def route(self, task_id):
        return self.j.one('SELECT r.state,r.mode,d.owner,d.origin,d.claude_session FROM task_delivery d JOIN completion_routes r USING(group_id,completion_generation) WHERE d.task_id=?', (task_id,))

    async def test_input_to_a_running_task_queues_after_its_turn_as_a_mirror_owned_generation(self):
        live = self.rows['live']['worker_id']
        queued = await self.backend.input(live, self.body('np-in:1'))
        self.assertEqual({k: queued[k] for k in ('disposition', 'generation', 'delivery_owner')}, dict(disposition='queued', generation=2, delivery_owner='mirror'))
        self.assertNotIn('reason', queued)
        task = self.j.one('SELECT * FROM tasks WHERE task_id=?', (queued['task_id'],))
        self.assertEqual((task['state'], json.loads(task['task_json'])['origin'], json.loads(task['task_json'])['client_request_id']), ('QUEUED', 'human-claude-view', 'np-in:1'))
        self.assertEqual(self.route(queued['task_id']), dict(state='MIRROR_PENDING', mode='wait', owner='mirror', origin='human-claude-view', claude_session=SESSION))
        (worker, frame), = self.frames
        self.assertEqual((worker, frame['operation'], frame['task_id']), (live, 'task', queued['task_id']))
        self.assertEqual(frame['input'], dict(client_request_id='np-in:1', origin='human-claude-view', mode='queue', preview='please also check the tests'))
        self.assertEqual(await self.backend.input(live, self.body('np-in:1')), queued)
        self.assertEqual(len(self.frames), 1)
        with self.assertRaises(PeerError) as conflict:
            await self.backend.input(live, self.body('np-in:1', text='something else'))
        self.assertEqual(conflict.exception.code, 'IDEMPOTENCY_CONFLICT')
        second = await self.backend.input(live, self.body('np-in:2', origin='parent-model'))
        self.assertEqual((second['generation'], [f['task_id'] for _, f in self.frames]), (3, [queued['task_id'], second['task_id']]))

    async def test_rapid_inputs_are_forwarded_once_each_in_order(self):
        live = self.rows['live']['worker_id']
        results = await asyncio.gather(*[self.backend.input(live, self.body(f'np-in:r{i}', text=f'follow-up {i}')) for i in range(4)],
                                       self.backend.input(live, self.body('np-in:r0', text='follow-up 0')))
        self.assertEqual([r['generation'] for r in results[:4]], [2, 3, 4, 5])
        self.assertEqual(results[4], results[0])
        self.assertEqual([f['input']['preview'] for _, f in self.frames], [f'follow-up {i}' for i in range(4)])

    async def test_without_this_sessions_lease_a_follow_up_is_native_owned(self):
        stranger = NativeBackend(self.other, self.parent, new_id())
        queued = await stranger.input(self.rows['live']['worker_id'], self.body('np-in:x'))
        self.assertEqual(queued['delivery_owner'], 'native')
        self.assertEqual(self.route(queued['task_id'])['state'], 'UNCLAIMED')

    async def test_steer_is_queued_with_a_reason_unless_the_provider_steers(self):
        unsupported = await self.backend.input(self.rows['live']['worker_id'], self.body('np-in:s1', mode='steer'))
        self.assertEqual((unsupported['disposition'], unsupported['reason']), ('queued', 'steer-unsupported'))
        steering = NativeBackend(self.other, self.parent, SESSION, runtimes={'codex': next(iter(STEER_RUNTIMES['codex']))})
        steered = await steering.input(self.rows['steering']['worker_id'], self.body('np-in:s2', mode='steer', origin='parent-model'))
        self.assertEqual({k: steered[k] for k in ('disposition', 'task_id', 'generation')}, dict(disposition='steered', task_id=self.rows['steering']['task_id'], generation=1))
        frame = self.frames[-1][1]
        self.assertEqual((frame['operation'], frame['text'], frame['input']['mode']), ('steer', 'please also check the tests', 'steer'))
        self.assertEqual(len(self.j.all('SELECT * FROM tasks WHERE worker_id=?', (self.rows['steering']['worker_id'],))), 1)
        self.assertTrue(self.j.one("SELECT 1 FROM events WHERE kind='input-steered' AND subject_id=?", (self.rows['steering']['task_id'],)))

    async def test_a_steer_with_an_unobserved_outcome_is_never_sent_again(self):
        steering = NativeBackend(self.other, self.parent, SESSION, runtimes={'codex': next(iter(STEER_RUNTIMES['codex']))})
        worker = self.rows['steering']['worker_id']
        async def lost(target, frame):self.frames.append((target, frame))  # The steer is written; no outcome ever arrives.
        with patch.object(self.group, 'send_worker', side_effect=lost), patch('nativepeers.supervisor.STEER_SECONDS', 0.2):
            with self.assertRaises(PeerError) as unknown:
                await steering.input(worker, self.body('np-in:u', mode='steer'))
        self.assertEqual((unknown.exception.code, unknown.exception.may_have_received), ('DELIVERY_UNKNOWN', True))
        with self.assertRaises(PeerError) as again:
            await steering.input(worker, self.body('np-in:u', mode='steer'))
        self.assertEqual(again.exception.code, 'DELIVERY_UNKNOWN')
        self.assertEqual([f['operation'] for _, f in self.frames], ['steer'])

    async def test_input_in_follow_up_grace_starts_a_new_generation(self):
        queued = await self.backend.input(self.rows['grace']['worker_id'], self.body('np-in:g'))
        self.assertEqual((queued['disposition'], queued['generation'], queued['delivery_owner']), ('queued', 2, 'native'))

    async def test_input_to_a_released_worker_resumes_it_through_the_owned_resume_path(self):
        released = self.rows['released']['worker_id']
        resumed = await self.backend.input(released, self.body('np-in:res'))
        self.assertEqual({k: resumed[k] for k in ('disposition', 'generation', 'delivery_owner')}, dict(disposition='resumed', generation=2, delivery_owner='mirror'))
        self.released.assert_called()
        row = self.j.one('SELECT state,generation FROM workers WHERE worker_id=?', (released,))
        self.assertEqual((row['state'], row['generation']), ('RESERVED', 2))
        self.assertEqual(self.route(resumed['task_id'])['state'], 'MIRROR_PENDING')
        self.assertEqual(json.loads(self.j.one('SELECT task_json FROM tasks WHERE task_id=?', (resumed['task_id'],))['task_json'])['origin'], 'human-claude-view')
        self.assertEqual(self.j.one('SELECT client_request_id FROM resume_requests')['client_request_id'], 'np-in:res')
        self.assertEqual(self.frames[-1][1]['task_id'], resumed['task_id'])
        self.assertEqual(await self.backend.input(released, self.body('np-in:res')), resumed)

    async def test_a_follow_up_refused_by_a_releasing_controller_resumes_the_released_history(self):
        grace = self.rows['grace']['worker_id']
        async def releasing(payload):
            self.j.db.execute("UPDATE workers SET state='RELEASED_RESUMABLE' WHERE worker_id=?", (grace,))
            raise PeerError('TARGET_OFFLINE', 'Group controller is shutting down')
        with patch.object(self.group, 'enqueue', side_effect=releasing):
            resumed = await self.backend.input(grace, self.body('np-in:late'))
        self.assertEqual((resumed['disposition'], resumed['generation']), ('resumed', 2))
        self.assertTrue(self.j.one("SELECT 1 FROM events WHERE kind='input-origin'"))

    async def test_an_ownership_conflict_is_refused_once_and_never_retried_as_another_writer(self):
        self.released.side_effect = PeerError('OWNERSHIP_CONFLICT', 'A registered native runtime currently owns this history')
        released = self.rows['released']['worker_id']
        refused = await self.backend.input(released, self.body('np-in:c'))
        self.assertEqual((refused['disposition'], refused['reason'], refused['task_id']), ('refused', 'OWNERSHIP_CONFLICT', None))
        self.released.side_effect = None
        self.assertEqual(await self.backend.input(released, self.body('np-in:c')), refused)
        self.assertEqual(len(self.j.all('SELECT * FROM tasks WHERE worker_id=?', (released,))), 1)
        self.assertEqual(self.frames, [])

    async def test_interrupt_and_cancel_reach_only_the_current_task(self):
        live = self.rows['live']['worker_id'];task = self.rows['live']['task_id']
        interrupted = await self.backend.interrupt(live, dict(client_request_id='np-int:1'))
        self.assertEqual((interrupted['state'], interrupted['task_id'], interrupted['generation']), ('INTERRUPT_REQUESTED', task, 1))
        self.assertEqual(await self.backend.interrupt(live, dict(client_request_id='np-int:1')), interrupted)
        self.assertEqual([f['operation'] for _, f in self.frames], ['interrupt-task'])
        self.assertEqual((await self.backend.interrupt(self.rows['grace']['worker_id'], dict(client_request_id='np-int:2')))['state'], 'NOT_RUNNING')
        queued = await self.backend.input(live, self.body('np-in:behind'))
        self.assertEqual((await self.backend.interrupt(live, dict(client_request_id='np-int:3')))['task_id'], task)
        canceled = await self.backend.cancel_worker(live, dict(client_request_id='np-kill:1', reason='Stopped from Claude Code'))
        self.assertEqual((canceled['state'], canceled['task_id'], canceled['canceled']), ('CANCEL_REQUESTED', task, [task, queued['task_id']]))
        self.assertEqual(self.j.one('SELECT state FROM tasks WHERE task_id=?', (queued['task_id'],))['state'], 'CANCELED')
        self.assertEqual(await self.backend.cancel_worker(live, dict(client_request_id='np-kill:1', reason='Stopped from Claude Code')), canceled)
        self.assertEqual([(w, f['operation']) for w, f in self.frames], [(live, 'interrupt-task'), (live, 'task'), (live, 'interrupt-task'), (live, 'cancel-task')])
        self.assertEqual(self.j.one('SELECT state FROM groups')['state'], 'RUNNING')

    async def test_group_cancel_is_the_existing_cascading_cancel(self):
        value = await self.backend.cancel_group(self.gid, dict(client_request_id='np-stop-all:1', reason='Stop all'))
        self.assertEqual(value['state'], 'CANCEL_REQUESTED')
        self.assertEqual(sorted(value['workers']), sorted(r['worker_id'] for r in self.rows.values()))
        with self.assertRaises(PeerError):
            await NativeBackend(self.other, Registry(self.other).register('claude', self.root / 'x', new_id(), 'other', identity(self.group_child_owner()).json(), str(self.root), {}),
                                'x').cancel_group(self.gid, dict(client_request_id='y'))

    async def test_cli_equivalents_use_the_same_handlers_and_json(self):
        from nativepeers.cli import dispatch, parser
        live = self.rows['live']['worker_id']
        run = lambda *argv: dispatch(parser().parse_args([*argv, '--claude-session', SESSION, '--json']), self.other)
        queued = await run('input', live, '--message', 'from the terminal', '--origin', 'parent-model', '--client-request-id', 'cli-1', '--delivery-owner', 'mirror')
        self.assertEqual((queued['disposition'], queued['generation'], queued['delivery_owner']), ('queued', 2, 'mirror'))
        same = dict(client_request_id='cli-1', text='from the terminal', origin='parent-model', mode='queue', delivery_owner='mirror')
        self.assertEqual(await self.backend.input(live, same), queued)
        self.assertEqual((await run('interrupt', live, '--client-request-id', 'cli-int'))['task_id'], self.rows['live']['task_id'])
        self.assertEqual((await run('mirror', 'attach', '--worker-id', self.rows['grace']['worker_id'], '--check-only'))['mirror_owner'], None)
        self.assertEqual((await run('mirror', 'surfacing', '--worker-id', live, '--generation', '2'))['route'], 'MIRROR_SURFACING')
        self.assertEqual((await run('mirror', 'ack', '--worker-id', live, '--task-id', queued['task_id'], '--generation', '2'))['route'], 'MIRROR_DELIVERED')
        status = await run('mirror', 'status')
        self.assertEqual(sorted(l['worker_id'] for l in status['leases']), sorted(self.rows[n]['worker_id'] for n in ('live', 'released', 'steering')))
        self.assertTrue((await run('mirror', 'release', '--worker-id', live))['released'])

    def group_child_owner(self):
        process = subprocess.Popen(['/bin/sleep', '60']);self.addCleanup(lambda: (process.terminate(), process.wait(timeout=5)))
        return process.pid


FAKE_CLAUDE = """
import json,sys,time
args=sys.argv[1:];native=args[args.index('--session-id' if '--session-id' in args else '--resume')+1];task=sys.stdin.read()
def emit(value):print(json.dumps(value),flush=True)
emit({'type':'system','subtype':'init','session_id':native,'model':args[args.index('--model')+1],'tools':['Read']})
time.sleep(.1)
emit({'type':'assistant','session_id':native,'message':{'content':[{'type':'text','text':'working on it'}]}})
emit({'type':'result','subtype':'success','session_id':native,'result':'fixture answer','num_turns':1,'is_error':False})
"""


class EndToEndTests(unittest.IsolatedAsyncioTestCase):
    """A Path A worker under a real group supervisor and controller with a fake Claude: bind, stream, deliver, follow up."""

    async def test_a_mirrored_worker_streams_completes_without_a_push_and_takes_a_follow_up(self):
        import sys
        from nativepeers.compatibility import fingerprint
        from nativepeers.delegation import Delegation
        temporary = tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve();home = root / 'home';source = root / 'source';source.mkdir()
        binary = home / '.local/bin/claude';binary.parent.mkdir(parents=True);binary.write_text('#!' + sys.executable + '\n' + FAKE_CLAUDE);binary.chmod(0o700)
        journal = Journal(root / 'state');self.addCleanup(journal.close);reader = Journal(journal.root);self.addCleanup(reader.close)
        atomic_json(journal.root / 'compat/runtime.json', {'providers': {'claude': fingerprint(binary)}})
        session = new_id()
        parent = Registry(journal).register('claude', home, session, 'e2e parent', identity(os.getpid()).json(), str(source), {'kind': 'claude-inbox'},
                                            policy={'profiles': ['analysis'], 'max_depth': 1, 'max_concurrent_workers': 4})
        groups = []
        async def start(group_id):
            group = Group(journal, group_id);groups.append(group);await group.initialize()
            group.watch = asyncio.create_task(group.monitor())
        self.enterContext(patch('nativepeers.supervisor.PYTHON', Path(sys.executable)))
        self.enterContext(patch.dict(os.environ, {'NPEER_HOME': str(journal.root)}))
        self.enterContext(patch('nativepeers.delegation.Path.home', return_value=home))
        self.enterContext(patch.object(Delegation, 'ensure_supervisor', side_effect=start))
        backend = NativeBackend(reader, parent, session, runtimes={'claude': 'fixture'})
        try:
            await backend.spawn(dict(client_request_id=f'np:{session}:agent1', claude_agent_id='agent1', mirror=dict(owner='claude-mod', claude_session=session, claude_agent_id='agent1'),
                                     worker=dict(provider='claude', title='e2e', task='answer briefly', model='claude-fixture')))
            bound = await backend.binding('agent1', 25000)
            self.assertEqual(bound['state'], 'bound', bound)
            worker = bound['worker_id'];events = [];cursor = 0
            async def until(predicate):
                nonlocal cursor
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    page = await backend.activity(worker, cursor, 2000, 512, None)
                    events.extend(page['events']);cursor = max([cursor] + [e['seq'] for e in page['events']])
                    if any(predicate(e) for e in events):return
                self.fail('activity did not arrive: %r' % [e['kind'] for e in events])
            await until(lambda e: e['kind'] == 'task.terminal')
            terminal = [e for e in events if e['kind'] == 'task.terminal'][0]
            self.assertEqual((terminal['generation'], terminal['data']['status']), (1, 'completed'))
            self.assertIn('message.completed', [e['kind'] for e in events])
            await asyncio.sleep(1.2)  # A monitor cycle has offered the completed phase for delivery.
            self.assertEqual(reader.one('SELECT state FROM completion_routes WHERE completion_generation=1')['state'], 'MIRROR_PENDING')
            self.assertEqual(reader.all("SELECT * FROM events WHERE kind='parent-completion-submitted'"), [])
            self.assertEqual((await backend.mirror('surfacing', dict(worker_id=worker, task_id=terminal['task_id'], generation=1)))['route'], 'MIRROR_SURFACING')
            self.assertEqual((await backend.mirror('ack', dict(worker_id=worker, task_id=terminal['task_id'], generation=1)))['route'], 'MIRROR_DELIVERED')
            result = await backend.result(worker)
            self.assertEqual((result['model_observed'], result['status'], result['generation']), ('claude-fixture', 'completed', 1))
            queued = await backend.input(worker, dict(client_request_id='np-in:e2e', text='one more thing', origin='human-claude-view', mode='queue', delivery_owner='mirror'))
            self.assertEqual((queued['disposition'], queued['generation'], queued['delivery_owner']), ('queued', 2, 'mirror'))
            await until(lambda e: e['kind'] == 'task.terminal' and e['generation'] == 2)
            started = [e for e in events if e['kind'] == 'task.started']
            self.assertEqual([(e['generation'], e['data']['origin']) for e in started], [(1, 'parent-model'), (2, 'human')])
            self.assertEqual(started[1]['data']['client_request_id'], 'np-in:e2e')
            lineage = await backend.lineage(None, 0)
            self.assertEqual({k: lineage['workers'][0][k] for k in ('generation', 'status', 'mirrorOwner', 'agentId')}, dict(generation=2, status='completed', mirrorOwner='this', agentId='agent1'))
        finally:
            for group in groups:
                group.watch.cancel();await asyncio.gather(group.watch, return_exceptions=True)
                await group.close()


if __name__ == '__main__':
    unittest.main()
