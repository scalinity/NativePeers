"""The session bridge's NativePeers backend: binding, token, every route on a temporary journal, long-polls and exit conditions."""
import asyncio
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

from nativepeers import mirror
from nativepeers.activity import Recorder
from nativepeers.bridge import Bridge, socket_path
from nativepeers.bridge_backend import NativeBackend, bind_session, live_bridges, manifest_path
from nativepeers.compatibility import fingerprint
from nativepeers.delegation import Delegation
from nativepeers.errors import PeerError
from nativepeers.files import atomic_json
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity, matches
from nativepeers.registry import Registry

ROOT = Path(__file__).resolve().parents[2]
PYTHON = Path(sys.executable)


async def http(path, method, target, body=None, token=None):
    reader, writer = await asyncio.open_unix_connection(str(path))
    raw = json.dumps(body).encode() if body is not None else b''
    head = f'{method} {target} HTTP/1.1\r\nHost: np\r\nContent-Length: {len(raw)}\r\n' + (f'Authorization: Bearer {token}\r\n' if token else '') + '\r\n'
    writer.write(head.encode() + raw);await writer.drain()
    data = await reader.read();writer.close()
    return int(data.split(b' ', 2)[1]), json.loads(data.split(b'\r\n\r\n', 1)[1])


def sleeper(case):
    process = subprocess.Popen(['/bin/sleep', '60'])
    case.addCleanup(lambda: (process.terminate(), process.wait(timeout=5)))
    return identity(process.pid).json()


class BackendCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve();self.home = self.root / 'home';self.source = self.root / 'source';self.source.mkdir()
        binary = self.home / '.local/bin/claude';binary.parent.mkdir(parents=True);binary.write_text('metadata fixture only')
        (self.home / '.claude').mkdir();(self.home / '.claude/settings.json').write_text('{"model":"sonnet"}')
        self.j = Journal(self.root / 'state');self.addCleanup(self.j.close);self.registry = Registry(self.j)
        atomic_json(self.j.root / 'compat/runtime.json', {'providers': {'claude': fingerprint(binary)}})
        self.session = new_id()
        self.peer = self.registry.register('claude', self.home / '.claude', self.session, 'bridge parent', identity(os.getpid()).json(), str(self.source),
                                           {'kind': 'claude-inbox'}, policy={'profiles': ['analysis', 'workspace'], 'max_depth': 1, 'max_concurrent_workers': 4})
        self.enterContext(patch('nativepeers.delegation.Path.home', return_value=self.home))
        self.enterContext(patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(self.home / '.claude'), 'NPEER_HOME': str(self.j.root)}))
        self.supervisor = self.enterContext(patch.object(Delegation, 'ensure_supervisor', new_callable=AsyncMock))
        self.backend = NativeBackend(self.j, self.peer, self.session, runtimes={'claude': 'fixture'})

    def worker_spec(self, **extra):
        return dict(dict(provider='claude', title='bridge worker', task='focused fixture'), **extra)

    async def spawn(self, agent='agent1'):
        body = dict(client_request_id=f'np:{self.session}:{agent}', claude_agent_id=agent, worker=self.worker_spec(),
                    mirror=dict(owner='claude-mod', claude_session=self.session, claude_agent_id=agent))
        self.assertEqual((await self.backend.spawn(body))['state'], 'pending')
        bound = await self.backend.binding(agent, 5000)
        self.assertEqual(bound['state'], 'bound', bound)
        return body, bound


class BindingTests(BackendCase):
    async def test_binding_needs_one_live_session_whose_owner_is_an_ancestor(self):
        self.assertEqual(bind_session(self.j, self.registry, self.session)['handle'], self.peer['handle'])
        for session, owner, home in ((new_id(), None, None), (new_id(), sleeper(self), self.root / 'h2'), (self.session, sleeper(self), self.root / 'h3')):
            if owner:
                home.mkdir()
                self.registry.register('claude', home, session, 'other', owner, str(self.source), {'kind': 'claude-inbox'})
            with self.subTest(owner=bool(owner)), self.assertRaises(PeerError) as refused:
                bind_session(self.j, self.registry, session)
            self.assertEqual(refused.exception.code, 'CALLER_UNBOUND')

    def test_a_subprocess_bridge_binds_serves_and_exits_when_the_owner_dies_not_on_stdin_eof(self):
        short = Path(tempfile.mkdtemp(dir='/tmp', prefix='npb-'));self.addCleanup(shutil.rmtree, short, True)
        script = short / 'start.py'
        # The owner is a shell that stays the bridge's parent (bash: macOS /bin/sh re-executes); the bridge's own executable differs from it, as Claude's does.
        script.write_text('import os,sys\nfrom pathlib import Path\nimport nativepeers.bridge as b\nroot=Path(sys.argv[1])\nb.socket_directory=lambda uid=None:root\n'
                          'from nativepeers.cli import main\nsys.argv=["npeer","bridge","--claude-session",sys.argv[2],"--protocol","1","--json"]\nmain()\n')
        session = new_id();ready = short / 'go'
        owner = subprocess.Popen(['/bin/bash', '-c', f'while [ ! -e {ready} ]; do sleep 0.05; done; "{PYTHON}" {script} {short} {session}; echo done'],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=dict(os.environ, PYTHONPATH=str(ROOT), NPEER_HOME=str(self.j.root)),
                                 start_new_session=True)
        self.addCleanup(lambda: owner.poll() is None and (owner.kill(), owner.wait()))
        home = self.root / 'h-sub';home.mkdir()
        self.registry.register('claude', home, session, 'subprocess parent', identity(owner.pid).json(), str(self.source), {'kind': 'claude-inbox'})
        ready.touch()
        line = json.loads(owner.stdout.readline())
        self.assertTrue(line['ready'], line);self.assertEqual(line['protocol'], 1)
        socket = Path(line['socket']);self.assertTrue(socket.exists())
        self.assertEqual(oct(socket.stat().st_mode & 0o777), '0o600')
        bridges = live_bridges(self.j);self.assertEqual([b['claude_sessions'] for b in bridges], [[session]])
        bridge = identity(bridges[0]['pid']).json()
        status, envelope = asyncio.run(http(socket, 'GET', '/v1/hello', token=line['token']))
        self.assertEqual((status, envelope['data']['peer']), (200, line['peer']))
        time.sleep(1.5)  # Its standard input reached EOF at once; the bridge is still serving.
        self.assertTrue(socket.exists())
        os.kill(owner.pid, 9);owner.wait()
        deadline = time.monotonic() + 8
        while matches(bridge) and time.monotonic() < deadline:time.sleep(0.1)
        self.assertFalse(matches(bridge))
        self.assertFalse(socket.exists())
        self.assertEqual(live_bridges(self.j), [])

    def test_a_nearer_process_with_the_owners_executable_is_an_enclosing_session(self):
        short = Path(tempfile.mkdtemp(dir='/tmp', prefix='npb-'));self.addCleanup(shutil.rmtree, short, True)
        result = subprocess.run([str(PYTHON), '-m', 'nativepeers.cli', 'bridge', '--claude-session', self.session, '--protocol', '1', '--json'],
                                capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=str(ROOT), NPEER_HOME=str(self.j.root)), timeout=30)
        self.assertEqual(result.returncode, 4)
        line = json.loads(result.stdout.splitlines()[0])
        self.assertEqual((line['ready'], line['error']['code']), (False, 'CALLER_UNBOUND'))
        self.assertIn('enclosing', line['error']['message'])


class ServerTests(BackendCase):
    async def serve(self, **options):
        short = Path(tempfile.mkdtemp(dir='/tmp', prefix='npb-'));self.addCleanup(shutil.rmtree, short, True)
        out = io.StringIO();bridge = Bridge(self.backend, socket_path(self.session, root=short), out=out, **options)
        task = asyncio.create_task(bridge.run())
        while not out.getvalue().endswith('\n'):await asyncio.sleep(0.01)
        return bridge, task, json.loads(out.getvalue())

    async def test_the_token_is_required_and_every_answer_is_the_cli_envelope(self):
        bridge, task, ready = await self.serve()
        try:
            for token in (None, 'wrong'):
                status, envelope = await http(bridge.path, 'GET', '/v1/hello', token=token)
                self.assertEqual((status, envelope['ok'], envelope['error']['code']), (401, False, 'CALLER_UNBOUND'))
            status, envelope = await http(bridge.path, 'GET', '/v1/hello', token=ready['token'])
            self.assertEqual((status, envelope['ok'], envelope['schema_version']), (200, True, 1))
            self.assertEqual(envelope['data']['providers'], ['claude'])
            self.assertEqual(envelope['data']['capabilities']['activity_schema'], 'nativepeers.activity.v1')
            self.assertFalse(envelope['data']['capabilities']['steer']['codex'])
            status, envelope = await http(bridge.path, 'POST', '/v1/rebind', {'claude_session': self.session}, token=ready['token'])
            self.assertEqual(envelope['data']['rebound'], False)
            self.assertEqual(ready['peer'], self.peer['handle'])
        finally:
            bridge.stop('test');await task

    async def test_every_route_answers_over_http(self):
        bridge, task, ready = await self.serve()
        call = lambda method, target, body=None: http(bridge.path, method, target, body, token=ready['token'])
        try:
            agent = 'agentH';spawned = dict(client_request_id=f'np:{self.session}:{agent}', claude_agent_id=agent, worker=self.worker_spec(),
                                            mirror=dict(owner='claude-mod', claude_session=self.session, claude_agent_id=agent))
            self.assertEqual((await call('POST', '/v1/heartbeat', {}))[0], 200)
            self.assertIn('claude', (await call('GET', '/v1/catalog'))[1]['data']['providers'])
            self.assertEqual((await call('POST', '/v1/spawn', dict(check_only=True, worker=self.worker_spec())))[1]['data']['ok'], True)
            self.assertEqual((await call('POST', '/v1/spawn', spawned))[1]['data']['state'], 'pending')
            bound = (await call('GET', f'/v1/bindings/{agent}?wait_ms=5000'))[1]['data'];worker = bound['worker_id']
            self.assertEqual(bound['state'], 'bound')
            self.assertEqual((await call('GET', '/v1/lineage?wait_ms=0'))[1]['data']['workers'][0]['workerId'], worker)
            self.assertEqual([l['worker_id'] for l in (await call('GET', '/v1/mirror/status'))[1]['data']['leases']], [worker])
            self.assertEqual((await call('POST', '/v1/mirror/attach', dict(worker_id=worker, check_only=True)))[1]['data']['mirror_owner'], 'this')
            self.assertEqual((await call('POST', '/v1/mirror/fault', dict(claude_agent_id=agent, kind='x', message='y')))[1]['data']['recorded'], True)
            self.assertEqual((await call('GET', f'/v1/workers/{worker}/activity?after=0&max=8'))[1]['data']['events'], [])
            self.assertEqual((await call('POST', f'/v1/workers/{worker}/interrupt', dict(client_request_id='i1')))[1]['data']['state'], 'NOT_RUNNING')
            for method, target, body, code in (('GET', f'/v1/workers/{worker}/result', None, 'PROTOCOL_ERROR'), ('GET', f'/v1/workers/{worker}/resume-command', None, 'OWNERSHIP_CONFLICT'),
                                               ('GET', '/v1/artifacts/art_missing', None, 'PROTOCOL_ERROR'), ('POST', f'/v1/workers/{worker}/cancel', dict(client_request_id='c1'), 'SUPERVISOR_LOST'),
                                               ('POST', f"/v1/groups/{bound['group_id']}/cancel", dict(client_request_id='g1'), 'SUPERVISOR_LOST'),
                                               ('POST', f'/v1/workers/{worker}/input', dict(client_request_id='n1', text='x', origin='robot', mode='queue'), 'PROTOCOL_ERROR')):
                with self.subTest(target=target):
                    status, envelope = await call(method, target, body)
                    self.assertEqual((status, envelope['ok'], envelope['error']['code']), (200, False, code))
            self.assertEqual((await call('GET', '/v1/workers/../etc'))[0], 404)
            self.assertEqual((await call('POST', '/v1/shutdown', {}))[1]['data']['stopping'], True)
            self.assertEqual(await asyncio.wait_for(task, 5), 'shutdown')
        finally:
            bridge.stop('test');await task

    async def test_it_exits_on_owner_death_heartbeat_loss_and_idle(self):
        for options, reason in ((dict(owner_check=0.1), 'owner-exited'), (dict(heartbeat_loss=0.3), 'heartbeat-lost'), (dict(idle=0.3), 'idle')):
            with self.subTest(reason=reason):
                if reason == 'owner-exited':self.backend.owner_alive = AsyncMock(side_effect=[True, False])
                else:self.backend.owner_alive = AsyncMock(return_value=True)
                bridge, task, _ = await self.serve(**options)
                self.assertEqual(await asyncio.wait_for(task, 5), reason)
                self.assertFalse(bridge.path.exists())

    async def test_leases_are_renewed_only_while_heartbeats_arrive_and_keep_the_bridge_from_idling(self):
        await self.spawn()
        lease = mirror.live_lease(self.j, (await self.backend.binding('agent1', 0))['worker_id'])
        self.backend.owner_alive = AsyncMock(return_value=True)
        self.backend.renew_leases = AsyncMock(wraps=self.backend.renew_leases)
        bridge, task, ready = await self.serve(idle=0.2, lease_renew=0.1)
        try:
            await asyncio.sleep(1.2)
            self.assertFalse(task.done())
            renewed = self.backend.renew_leases.await_count;self.assertGreater(renewed, 0)
            bridge.last_heartbeat -= 40
            await asyncio.sleep(1.2)
            self.assertEqual(self.backend.renew_leases.await_count, renewed)
        finally:
            bridge.stop('test');await task
        self.assertGreater(mirror.live_lease(self.j, lease['worker_id'])['expires_at'], lease['expires_at'])


class RouteTests(BackendCase):
    async def test_spawn_check_returns_labels_and_refusals_raise(self):
        checked = await self.backend.spawn({'check_only': True, 'worker': self.worker_spec()})
        self.assertEqual(checked, dict(ok=True, model='sonnet', effort=None, profile='analysis'))
        self.j.db.execute('UPDATE peer_instances SET descriptor_json=json_set(descriptor_json,\'$.policy.profiles\',json(\'["analysis"]\')) WHERE peer_key=?', (self.peer['handle'],))
        with self.assertRaises(PeerError) as refused:
            await self.backend.spawn({'check_only': True, 'worker': self.worker_spec(permission_profile='workspace')})
        self.assertEqual(refused.exception.code, 'PERMISSION_CAPABILITY_UNKNOWN')
        self.assertEqual(self.j.all('SELECT * FROM groups'), [])

    async def test_a_path_a_spawn_is_one_mirrored_wait_group_bound_once_by_agent(self):
        body, bound = await self.spawn()
        self.assertEqual({k: bound[k] for k in ('generation', 'model', 'profile')}, dict(generation=1, model='sonnet', profile='analysis'))
        group = self.j.one('SELECT * FROM groups');policy = json.loads(group['policy_json'])
        self.assertEqual((policy['completion_mode'], policy['original_request']['workers'][0]['mirror']['claude_agent_id']), ('wait', 'agent1'))
        self.assertNotIn('cwd', policy['original_request']['workers'][0])
        self.assertEqual(self.j.one('SELECT state,mode FROM completion_routes'), dict(state='MIRROR_PENDING', mode='wait'))
        self.assertEqual(self.j.one('SELECT owner,claude_session,claude_agent_id FROM task_delivery'), dict(owner='mirror', claude_session=self.session, claude_agent_id='agent1'))
        self.assertEqual(mirror.live_lease(self.j, bound['worker_id'])['claude_session'], self.session)
        self.assertEqual((await self.backend.spawn(body))['worker_id'], bound['worker_id'])
        self.assertEqual(len(self.j.all('SELECT * FROM groups')), 1)
        with self.assertRaises(PeerError) as conflict:
            await self.backend.spawn(dict(body, worker=self.worker_spec(task='different')))
        self.assertEqual(conflict.exception.code, 'IDEMPOTENCY_CONFLICT')
        restarted = NativeBackend(self.j, self.peer, self.session)
        self.assertEqual((await restarted.binding('agent1', 0))['worker_id'], bound['worker_id'])
        started = time.monotonic()
        self.assertEqual(await restarted.binding('nobody', 300), dict(state='pending'))
        self.assertGreaterEqual(time.monotonic() - started, 0.25)

    async def test_a_worker_cwd_must_be_the_sessions_directory(self):
        for body in ({'check_only': True, 'worker': self.worker_spec(cwd='/somewhere/else')},
                     dict(client_request_id='np:s:agentC', claude_agent_id='agentC', worker=self.worker_spec(cwd=str(self.root)), mirror=dict(owner='claude-mod'))):
            with self.subTest(check=body.get('check_only', False)), self.assertRaises(PeerError) as refused:
                await self.backend.spawn(body)
            self.assertEqual((refused.exception.code, refused.exception.message), ('PROTOCOL_ERROR', f"np workers run in the session's directory ({self.source})"))
        self.assertEqual((self.j.all('SELECT * FROM groups'), self.j.all('SELECT * FROM bridge_requests')), ([], []))
        for same in (str(self.source), str(self.source) + '/.', '.'):
            with self.subTest(cwd=same):
                self.assertTrue((await self.backend.spawn({'check_only': True, 'worker': self.worker_spec(cwd=same)}))['ok'])

    async def test_a_task_moved_by_fallback_is_reported_on_the_binding_and_the_lineage_row(self):
        _, bound = await self.spawn()
        replacement = new_id()
        self.j.db.execute('INSERT INTO workers SELECT ?,group_id,NULL,\'antigravity\',NULL,role_title,\'gemini\',NULL,NULL,cwd,NULL,\'RESERVED\',1,lease_json FROM workers WHERE worker_id=?',
                          (replacement, bound['worker_id']))
        self.j.db.execute("UPDATE tasks SET state='FALLBACK_PENDING' WHERE task_id=?", (bound['task_id'],))
        self.assertEqual((await self.backend.binding('agent1', 0))['continued_by'], dict(state='pending', from_generation=1, task_id=bound['task_id']))
        self.j.db.execute('INSERT INTO task_attempts VALUES(?,?,?,?,?)', (bound['task_id'], 0, bound['worker_id'], 'FAILED', 'art_x'))
        self.j.db.execute("UPDATE tasks SET worker_id=?,state='QUEUED' WHERE task_id=?", (replacement, bound['task_id']))
        moved = dict(state='moved', from_generation=None, worker_id=replacement, task_id=bound['task_id'], generation=1)
        self.assertEqual((await self.backend.binding('agent1', 0))['continued_by'], moved)
        rows = {r['workerId']: r for r in (await self.backend.lineage(None, 0))['workers']}
        self.assertEqual((rows[bound['worker_id']]['continuedBy'], rows[replacement]['continuedBy']), (moved, None))

    async def test_a_finished_worker_without_a_log_is_served_from_its_native_history_and_a_new_one_is_not(self):
        _, bound = await self.spawn()
        clock = time.monotonic();fresh = await self.backend.activity(bound['worker_id'], 0, 300, 512, None)
        self.assertEqual((fresh['events'], fresh.get('backfill')), ([], None));self.assertGreaterEqual(time.monotonic() - clock, 0.25)
        self.j.db.execute("UPDATE workers SET state='RELEASED_RESUMABLE'");self.j.db.execute("UPDATE tasks SET state='COMPLETED'")
        page = await self.backend.activity(bound['worker_id'], 0, 0, 512, None)
        self.assertTrue(page['backfill'])
        self.assertEqual([(e['kind'], e['source']['event']) for e in page['events']], [('task.started', 'backfill'), ('gap', 'backfill'), ('task.terminal', 'backfill')])
        self.assertEqual((page['events'][1]['data']['reason'], page['events'][2]['data']['status'], page['last_seq']), ('history-not-found', 'completed', 3))
        self.assertEqual([e['seq'] for e in (await self.backend.activity(bound['worker_id'], 1, 0, 1, None))['events']], [2])
        self.assertEqual([e['kind'] for e in (await self.backend.activity(bound['worker_id'], 0, 0, 512, 'none'))['events']], ['task.terminal'])
        clock = time.monotonic();done = await self.backend.activity(bound['worker_id'], 3, 300, 512, None)
        self.assertEqual((done['events'], done['next_seq']), ([], 3));self.assertGreaterEqual(time.monotonic() - clock, 0.25)

    async def test_a_failed_spawn_binds_as_failed_with_its_code(self):
        with patch.object(Delegation, 'spawn', AsyncMock(side_effect=PeerError('QUOTA_EXHAUSTED', 'weekly pool used'))):
            await self.backend.spawn(dict(client_request_id='np:s:agent2', claude_agent_id='agent2', worker=self.worker_spec(), mirror=dict(owner='claude-mod')))
            failed = await self.backend.binding('agent2', 5000)
        self.assertEqual(failed, dict(state='failed', error=dict(code='QUOTA_EXHAUSTED', message='weekly pool used')))

    async def test_a_mirror_for_another_session_or_a_batch_is_refused_by_spawn(self):
        caller = self.backend.caller()
        for workers, mode, code in (([self.worker_spec(), self.worker_spec()], 'wait', 'PROTOCOL_ERROR'), ([self.worker_spec()], 'push', 'PROTOCOL_ERROR')):
            for worker in workers:
                worker['mirror'] = dict(owner='claude-mod', claude_session=self.session, claude_agent_id='a')
            with self.subTest(mode=mode, workers=len(workers)), self.assertRaises(PeerError) as refused:
                await self.backend.core.delegation.spawn(caller, dict(workers=workers, completion_mode=mode), check=True)
            self.assertEqual(refused.exception.code, code)
        spec = self.worker_spec();spec['mirror'] = dict(owner='claude-mod', claude_session='someone-else', claude_agent_id='a')
        with self.assertRaises(PeerError) as refused:
            await self.backend.core.delegation.spawn(caller, dict(workers=[spec], completion_mode='wait'), check=True)
        self.assertEqual(refused.exception.code, 'APPROVAL_BLOCKED')

    async def test_lineage_long_polls_on_the_journal_and_reports_the_mirror_owner(self):
        _, bound = await self.spawn()
        first = await self.backend.lineage(None, 0)
        row = first['workers'][0]
        self.assertTrue(first['changed'])
        self.assertEqual({k: row[k] for k in ('workerId', 'groupId', 'taskId', 'generation', 'provider', 'status', 'agentId', 'mirrorOwner', 'parentWorkerId', 'profile')},
                         dict(workerId=bound['worker_id'], groupId=bound['group_id'], taskId=bound['task_id'], generation=1, provider='claude', status='starting',
                              agentId='agent1', mirrorOwner='this', parentWorkerId=None, profile='analysis'))
        self.assertIsInstance(row['startedAt'], int)
        started = time.monotonic();quiet = await self.backend.lineage(first['cursor'], 600)
        self.assertFalse(quiet['changed']);self.assertGreaterEqual(time.monotonic() - started, 0.55)
        other = Journal(self.j.root);self.addCleanup(other.close)
        async def change():
            await asyncio.sleep(0.3);other.db.execute("UPDATE tasks SET state='RUNNING'");other.db.execute("UPDATE mirror_leases SET claude_session='other'")
        started = time.monotonic();changer = asyncio.create_task(change())
        changed = await self.backend.lineage(first['cursor'], 10000);await changer
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual({k: changed['workers'][0][k] for k in ('status', 'mirrorOwner', 'agentId')}, dict(status='running', mirrorOwner='other', agentId=None))

    async def test_lineage_lists_a_workers_own_delegations_read_only(self):
        _, bound = await self.spawn()
        child_peer, child_group, child = 'np1_' + 'c' * 64, new_id(), new_id()
        self.j.db.execute('UPDATE workers SET peer_key=? WHERE worker_id=?', (child_peer, bound['worker_id']))
        self.j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)', (child_group, child_peer, json.dumps({'created_at': time.time()}), 'RUNNING', '{}'))
        self.j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (child, child_group, None, 'codex', None, 'nested', 'm', None, 'high', '/', None, 'RUNNING', 1, '{}'))
        rows = (await self.backend.lineage(None, 0))['workers']
        self.assertEqual([(r['workerId'], r['parentWorkerId']) for r in rows], [(bound['worker_id'], None), (child, bound['worker_id'])])
        with self.assertRaises(PeerError):
            await self.backend.result(child)

    async def test_doctor_reports_the_session_scoped_bridge_and_its_live_processes(self):
        from nativepeers.cli import dispatch, parser
        live = self.root / 'live.sock';live.touch()
        atomic_json(manifest_path(self.j, live), dict(process=identity(os.getpid()).json(), socket=str(live), peer=self.peer['handle'], claude_sessions=[self.session]))
        atomic_json(manifest_path(self.j, self.root / 'gone.sock'), dict(process=sleeper(self) | {'pid': 999999}, socket=str(live)))
        with patch('pathlib.Path.home', return_value=self.home), patch.dict(os.environ, {}, clear=True):
            report = await dispatch(parser().parse_args(['doctor', '--json']), self.j)
        self.assertEqual((report['bridge']['mode'], report['bridge']['daemon']), ('session-scoped', False))
        self.assertEqual([b['claude_sessions'] for b in report['bridge']['live']], [[self.session]])

    async def test_activity_reads_the_log_and_an_item_read_includes_its_generations_terminal(self):
        _, bound = await self.spawn()
        recorder = Recorder(self.j, bound['worker_id'], bound['group_id'], 'claude')
        recorder.begin(bound['task_id'], 1)
        recorder.emit('tool.started', dict(kind='exec', title='ls', input={}), item='c1')
        recorder.emit('message.delta', dict(text='hello'))
        recorder.terminal('interrupted', 'partial', error=dict(code='USER_INTERRUPTED', message='stopped'))
        await recorder.close()
        page = await self.backend.activity(bound['worker_id'], 0, 0, 512, None)
        kinds = [e['kind'] for e in page['events']]
        self.assertEqual(kinds, ['task.started', 'task.state', 'tool.started', 'message.delta', 'tool.completed', 'task.terminal'])
        self.assertEqual(page['last_seq'], page['events'][-1]['seq'])
        started = page['events'][2]['seq']
        item = await self.backend.activity(bound['worker_id'], started, 0, 64, 'c1')
        self.assertEqual([(e['kind'], e['data'].get('status')) for e in item['events']], [('tool.completed', 'interrupted'), ('task.terminal', 'interrupted')])
        clock = time.monotonic();idle = await self.backend.activity(bound['worker_id'], page['last_seq'], 400, 512, None)
        self.assertEqual(idle['events'], []);self.assertGreaterEqual(time.monotonic() - clock, 0.35)
        with self.assertRaises(PeerError):
            await NativeBackend(self.j, self.registry.register('claude', self.root, new_id(), 'stranger', sleeper(self), str(self.source), {}), 'x').activity(bound['worker_id'], 0, 0, 1, None)

    async def test_mirror_ops_attach_exclusively_and_record_faults(self):
        _, bound = await self.spawn()
        worker = bound['worker_id']
        self.assertEqual((await self.backend.mirror('attach', dict(worker_id=worker, check_only=True)))['mirror_owner'], 'this')
        stranger = NativeBackend(self.j, self.peer, 'other-session')
        with self.assertRaises(PeerError) as owned:
            await stranger.mirror('attach', dict(worker_id=worker, claude_agent_id='agent9', display_only=True))
        self.assertEqual(owned.exception.code, 'MIRROR_OWNED')
        self.assertEqual((await self.backend.mirror('surfacing', dict(worker_id=worker, generation=1)))['route'], 'MIRROR_SURFACING')
        self.assertEqual((await self.backend.mirror('ack', dict(worker_id=worker, task_id=bound['task_id'], generation=1)))['route'], 'MIRROR_DELIVERED')
        for op in ('fault', 'fallthrough'):
            self.assertTrue((await self.backend.mirror(op, dict(claude_agent_id='agent1', worker_id=worker, kind='budget', message='x', reason='usage')))['recorded'])
        self.assertEqual({e['kind'] for e in self.j.all("SELECT kind FROM events WHERE kind LIKE 'mirror-%'")} >= {'mirror-fault', 'mirror-fallthrough', 'mirror-surfacing', 'mirror-ack'}, True)
        status = await self.backend.mirror_status()
        self.assertEqual([l['worker_id'] for l in status['leases']], [worker])
        self.assertTrue((await self.backend.mirror('release', dict(worker_id=worker)))['released'])
        self.assertTrue(await stranger.mirror('attach', dict(worker_id=worker, claude_agent_id='agent9')))
        self.assertFalse(self.backend.has_leases())

    async def test_rebind_after_clear_needs_the_same_owner_and_keeps_old_workers_under_their_parent(self):
        _, bound = await self.spawn()
        cleared = new_id()
        foreign = self.root / 'foreign';foreign.mkdir()
        self.registry.register('claude', foreign, cleared, 'cleared elsewhere', sleeper(self), str(self.source), {'kind': 'claude-inbox'})
        with self.assertRaises(PeerError):
            await self.backend.rebind({'claude_session': cleared})
        renewed = new_id()
        fresh = self.registry.register('claude', self.home / '.claude', renewed, 'after clear', identity(os.getpid()).json(), str(self.source),
                                       {'kind': 'claude-inbox'}, policy={'profiles': ['analysis'], 'max_depth': 1, 'max_concurrent_workers': 4})
        value = await self.backend.rebind({'claude_session': renewed})
        self.assertEqual((value['peer'], value['previous'], value['rebound']), (fresh['handle'], self.peer['handle'], True))
        self.assertEqual(self.backend.caller().key, fresh['handle'])
        self.assertEqual(self.backend.worker(bound['worker_id'])['parent_key'], self.peer['handle'])
        self.assertEqual((await self.backend.lineage(None, 0))['workers'][0]['mirrorOwner'], 'this')

    async def test_result_artifact_and_resume_command_use_the_existing_records(self):
        from nativepeers.worker_process import Worker
        _, bound = await self.spawn()
        worker = Worker(self.j, bound['worker_id']);worker.emit = Mock();worker.bootstrap()
        worker.claude_turn = AsyncMock(return_value={'status': 'completed', 'text': 'exact fake result', 'native_turn_ids': []})
        await worker.task(bound['task_id'], 'fixture')
        result = await self.backend.result(bound['worker_id'])
        self.assertEqual({k: result[k] for k in ('task_id', 'generation', 'status', 'patch_artifact')}, dict(task_id=bound['task_id'], generation=1, status='completed', patch_artifact=None))
        self.assertEqual(result['changes'], dict(files=0, worktree=None, added=0, removed=0))
        self.assertEqual((result['provider'], result['model_requested']), ('claude', 'sonnet'))
        self.assertIsInstance(result['elapsed_ms'], int)
        page = await self.backend.artifact(result['result_artifact_id'], 0, 65536)
        self.assertEqual(json.loads(__import__('base64').b64decode(page['data_base64']))['summary'], 'exact fake result')
        with self.assertRaises(PeerError):
            await self.backend.resume_command(bound['worker_id'])
        self.j.db.execute("UPDATE workers SET state='RELEASED_RESUMABLE'")
        with patch('nativepeers.recovery.assert_released'):
            self.assertIn('claude --resume', (await self.backend.resume_command(bound['worker_id']))['command'])

    async def test_hello_and_catalog(self):
        hello = self.backend.hello()
        self.assertEqual((hello['peer'], hello['providers'], hello['capabilities']['backfill']), (self.peer['handle'], ['claude'], ['claude', 'codex']))
        steering = NativeBackend(self.j, self.peer, self.session, runtimes={'codex': next(iter(__import__('nativepeers.bridge_backend', fromlist=['STEER_RUNTIMES']).STEER_RUNTIMES['codex']))})
        self.assertTrue(steering.hello()['capabilities']['steer']['codex'])
        catalog = await self.backend.catalog()
        self.assertIn('sonnet', [m['id'] for m in catalog['providers']['claude']['models']])
        self.assertEqual(catalog['providers']['claude']['default_model'], 'sonnet')
        self.assertEqual(catalog['profiles'], ['analysis', 'workspace'])
        self.assertIs(await self.backend.catalog(), catalog)


if __name__ == '__main__':
    unittest.main()
