"""Offline CLI/core parity through the real bound MCP stdio facade."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from nativepeers.antigravity_install import allowed_tools
from nativepeers.caller import Caller
from nativepeers.core import Core
from nativepeers.errors import PeerError
from nativepeers.files import atomic_json
from nativepeers.journal import Journal
from nativepeers.mcp_server import TOOLS
from nativepeers.models import new_id
from nativepeers.pagination import encode
from nativepeers.process import identity


ROOT = Path(__file__).resolve().parents[2]


class AckGroupsFacadeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        scratch = ROOT / 'evidence/scratch'
        scratch.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.journal = Journal(self.root / 'state')
        self.addCleanup(self.journal.close)
        self.core = Core(self.journal)
        self.owner = identity(os.getpid()).json()
        self.grants = {'worker_id': 'analysis-worker', 'can_spawn': False}
        self.peer = self.core.registry.register(
            'codex', self.root / 'provider', new_id(), 'offline facade fixture',
            self.owner, str(self.root), {'kind': 'fake'}, owner_kind='test',
            policy=self.grants)
        self.caller = Caller('peer', self.peer['handle'], self.peer['incarnation_id'], self.grants)
        self.binding = self.journal.root / 'bindings/fixture'
        self.lease = dict(state='BOUND', kind='peer', provider='codex', owner=self.owner,
                          expires_at=time.time() + 120, peer_key=self.caller.key,
                          incarnation_id=self.caller.incarnation, grants=self.grants)
        atomic_json(self.binding, self.lease)

    def environment(self):
        return dict(os.environ, HOME=str(self.root / 'home'), NPEER_HOME=str(self.journal.root),
                    PYTHONPATH=str(ROOT), NPEER_BOOTSTRAP='', NPEER_BINDING='', CODEX_THREAD_ID='')

    @asynccontextmanager
    async def session(self, *, native=False, environment=None):
        args = ['-m', 'nativepeers.mcp_server']
        args += ['--native-caller'] if native else ['--binding', str(self.binding)]
        parameters = StdioServerParameters(command=sys.executable, args=args, cwd=ROOT,
                                          env=environment or self.environment())
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer, read_timeout_seconds=5) as session:
                await session.initialize()
                yield session

    async def mcp(self, session, name, args, error=None):
        result = await session.call_tool(name, args)
        envelope = json.loads(result.content[0].text)
        self.assertEqual(result.is_error, error is not None, envelope)
        self.assertEqual(envelope['ok'], error is None, envelope)
        if error:
            self.assertEqual(envelope['error']['code'], error)
            self.assertFalse(envelope['error']['may_have_received'])
            return envelope['error']
        self.assertIsNone(envelope['error'])
        return envelope['data']

    async def cli(self, *args, error=None, exit_code=0):
        environment = self.environment()
        environment['NPEER_BINDING'] = str(self.binding)
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'nativepeers', *args, '--json', cwd=ROOT, env=environment,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        self.assertEqual(stderr, b'')
        envelope = json.loads(stdout)
        self.assertEqual(envelope['ok'], error is None, envelope)
        if error:
            self.assertNotEqual(process.returncode, 0, envelope)
            self.assertEqual(envelope['error']['code'], error)
            return envelope['error']
        self.assertEqual(process.returncode, exit_code, envelope)
        return envelope['data']

    def message(self, state='SUBMITTED_UNCONFIRMED', *, recipient=None):
        message, attempt = new_id(), new_id()
        payload = {'message_id': message, 'body': {'text': 'offline fixture'}}
        self.journal.db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?)',
            (message, new_id(), 'sender', recipient or self.caller.key, None, None,
             json.dumps(payload), 'fixture', time.time() + 60, time.time()))
        self.journal.db.execute(
            'INSERT INTO attempts(attempt_id,message_id,target_incarnation,transport,state,started_at) VALUES(?,?,?,?,?,?)',
            (attempt, message, self.caller.incarnation, 'fake', state, time.time()))
        return message

    async def test_usage_status_cli_parity_is_bound_read_only_and_analysis_accessible(self):
        from unittest.mock import patch
        from nativepeers.usage import store
        from nativepeers.usage.normalize import pool
        with patch('pathlib.Path.home',return_value=self.root/'home'):
            store.record(self.journal,'codex',self.root/'provider',dict(source='fixture-native',route='subscription',
                models=[{'model':'exact'}],pools=[pool('short',used=25,models=['exact'])]))
        before=self.journal.all('SELECT * FROM usage_observations')
        async with self.session() as session:
            self.assertIn('usage_status',{t.name for t in (await session.list_tools()).tools})
            mcp=await self.mcp(session,'usage_status',{'provider':'codex','model':'exact'})
            cli=await self.cli('usage','--provider','codex','--model','exact')
            for view in (mcp,cli):
                self.assertEqual(view['providers'][0]['pools'][0]['remaining'],75)
                self.assertEqual(view['providers'][0]['models'][0]['model'],'exact')
                self.assertFalse(view['inference_submitted'])
            await self.mcp(session,'usage_status',{'provider':'codex','pools':[]},error='PROTOCOL_ERROR')
            self.assertTrue(allowed_tools('analysis','call_mcp_tool',{'ServerName':'nativepeers','ToolName':'usage_status'}))
        self.assertEqual(self.journal.all('SELECT * FROM usage_observations'),before)
        atomic_json(self.binding,dict(self.lease,state='RESERVED_UNBOUND',provider='claude'))
        async with self.session() as session:
            await self.mcp(session,'usage_status',{},error='CALLER_NOT_READY')

    def group(self, parent=None, route='UNCLAIMED', result=None):
        group, worker, task = new_id(), new_id(), new_id()
        parent = parent or self.caller.key
        self.journal.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',
                                (group, parent, '{}', 'COMPLETED', '{}'))
        self.journal.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (worker, group, None, 'codex', new_id(), 'retained worker', 'fixture', 'fixture',
             None, str(self.root), None, 'RELEASED_RESUMABLE', 1, '{}'))
        self.journal.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',
                                (task, worker, None, None, 'COMPLETED', result, None, 1, '{}'))
        self.journal.db.execute('INSERT INTO completion_routes VALUES(?,?,?,?,?,?,?,?,?)',
                                (group, 1, parent, 'push', route, None, None, new_id(), 1))
        return group

    def snapshot(self):
        return list(self.journal.db.iterdump())

    async def test_analysis_inventory_schema_validation_and_antigravity_gate(self):
        async with self.session() as session:
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            self.assertIn('peer_ack', tools)
            self.assertIn('task_groups', tools)
            self.assertNotIn('worker_spawn', tools)
            self.assertNotIn('task_cancel', tools)
            for name in ('peer_ack', 'task_groups'):
                schema = tools[name].input_schema
                self.assertEqual(schema, TOOLS[name][1])
                self.assertFalse(schema['additionalProperties'])
                self.assertTrue(allowed_tools('analysis', 'call_mcp_tool',
                    {'ServerName': 'nativepeers', 'ToolName': name}))
                self.assertFalse(allowed_tools('analysis', 'call_mcp_tool',
                    {'ServerName': 'other', 'ToolName': name}))
                for prefix in ('mcp_nativepeers_', 'mcp__nativepeers__'):
                    self.assertTrue(allowed_tools('analysis', prefix + name))
            self.assertEqual(tools['peer_ack'].input_schema['required'], ['message_id'])
            before = self.snapshot()
            for name, args in [
                ('peer_ack', {}), ('peer_ack', {'message_id': 1}),
                ('peer_ack', {'message_id': 'missing', 'from': 'other'}),
                ('task_groups', {'from': 'other'}), ('task_groups', {'parent_key': 'other'}),
                ('task_groups', {'limit': 0}), ('task_groups', {'limit': 101}),
                ('task_groups', {'limit': True}), ('task_groups', {'cursor': 1}),
            ]:
                with self.subTest(name=name, args=args):
                    await self.mcp(session, name, args, error='PROTOCOL_ERROR')
            self.assertEqual(self.snapshot(), before)

    async def test_ack_cli_core_parity_idempotency_recipient_scope_and_no_ingestion(self):
        self.group()
        routes = self.journal.all('SELECT * FROM completion_routes')
        async with self.session() as session:
            for state in ('OUTBOX_COMMITTED', 'SUBMITTING', 'HELD', 'SUBMITTED_UNCONFIRMED', 'DELIVERY_UNKNOWN', 'ACCEPTED_QUEUED'):
                with self.subTest(state=state):
                    message = self.message(state)
                    attempts = self.journal.all('SELECT * FROM attempts')
                    expected = await self.mcp(session, 'peer_ack', {'message_id': message})
                    self.assertEqual(expected, await self.core.tool(self.caller, 'peer_ack', {'message_id': message}))
                    exit_code = 0 if state in ('HELD', 'ACCEPTED_QUEUED') else 6
                    self.assertEqual(expected, await self.cli('ack', message, exit_code=exit_code))
                    self.assertEqual(expected, await self.mcp(session, 'peer_ack', {'message_id': message}))
                    self.assertEqual(expected['state'], state)
                    self.assertEqual(len(expected['receipts']), 1)
                    receipt = expected['receipts'][0]
                    self.assertEqual(receipt['stage'], 'MODEL_REPORTED_PROCESSED')
                    self.assertEqual(receipt['evidence_kind'], 'explicit-model-report')
                    self.assertIsNone(receipt['attempt_id'])
                    self.assertIsNone(receipt['source_native_id'])
                    self.assertEqual(self.journal.all('SELECT * FROM attempts'), attempts)
            foreign = self.message(recipient='other-recipient')
            before = self.snapshot()
            for message in (foreign, 'missing'):
                await self.mcp(session, 'peer_ack', {'message_id': message}, error='APPROVAL_BLOCKED')
                await self.cli('ack', message, error='APPROVAL_BLOCKED')
            self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.journal.all('SELECT * FROM completion_routes'), routes)
        self.assertEqual(self.journal.all('SELECT * FROM send_requests'), [])
        self.assertEqual(self.journal.all('SELECT * FROM events'), [])

    async def test_groups_cli_core_parity_stable_parent_pages_and_unchanged_claims(self):
        wanted = [self.group(route=route, result='retained-result')
                  for route in ('UNCLAIMED', 'OUTPUT_UNCERTAIN', 'DELIVERED')]
        self.group('other-parent')
        async with self.session() as session:
            before = self.snapshot()
            self.assertEqual(await self.mcp(session, 'task_groups', {}),
                             await self.core.tool(self.caller, 'task_groups', {}))
            page = await self.mcp(session, 'task_groups', {'limit': 1})
            self.assertEqual(page, await self.cli('groups', '--mine', '--limit', '1'))
            self.assertEqual(page, await self.core.tool(self.caller, 'task_groups', {'limit': 1}))
            self.assertEqual(self.snapshot(), before)
            later = self.group()
            before = self.snapshot()
            seen = [page['groups'][0]['group_id']]
            while page['next_cursor']:
                cursor = page['next_cursor']
                page = await self.mcp(session, 'task_groups', {'limit': 1, 'cursor': cursor})
                self.assertEqual(page, await self.cli('groups', '--mine', '--limit', '1', '--cursor', cursor))
                seen.extend(group['group_id'] for group in page['groups'])
            self.assertEqual(seen, wanted[::-1])
            self.assertNotIn(later, seen)
            self.assertTrue(page['groups'][0]['results_available'])
            self.assertEqual(page['groups'][0]['result_count'], 1)
            self.assertEqual(page['groups'][0]['workers'][0]['role_title'], 'retained worker')
            for cursor in ('not-json', encode(['groups', 'other-parent'], 1, 4),
                           encode(['groups', self.caller.key], 1, 10**100)):
                await self.mcp(session, 'task_groups', {'cursor': cursor}, error='PROTOCOL_ERROR')
                await self.cli('groups', '--mine', '--cursor', cursor, error='PROTOCOL_ERROR')
            self.assertEqual(self.snapshot(), before)

    async def test_native_parent_facade_uses_verified_caller_and_parent_scope(self):
        self.peer['policy'] = {'can_spawn': True, 'profiles': ['analysis']}
        self.journal.db.execute('UPDATE peer_instances SET descriptor_json=?', (json.dumps(self.peer),))
        self.group()
        self.group('other-parent')
        message = self.message()
        environment = self.environment()
        environment['CODEX_THREAD_ID'] = self.peer['peer_key']['native_session_id']
        async with self.session(native=True, environment=environment) as session:
            names = [tool.name for tool in (await session.list_tools()).tools]
            self.assertIn('peer_ack', names)
            self.assertIn('task_groups', names)
            self.assertIn('worker_spawn', names)
            self.assertIn('task_cancel', names)
            self.assertEqual(await self.mcp(session, 'task_groups', {}),
                             self.core.delegation.groups(self.caller))
            await self.mcp(session, 'peer_ack', {'message_id': message})

    async def test_inactive_fixed_and_native_bootstraps_refuse_without_side_effects(self):
        message = self.message()
        self.group()
        before = self.snapshot()
        atomic_json(self.binding, dict(self.lease, state='RESERVED_UNBOUND'))
        async with self.session() as session:
            for provider in ('codex', 'claude', 'antigravity'):
                atomic_json(self.binding, dict(self.lease, state='RESERVED_UNBOUND', provider=provider))
                names = [tool.name for tool in (await session.list_tools()).tools]
                self.assertEqual('peer_ack' in names, provider != 'codex')
                self.assertEqual('task_groups' in names, provider != 'codex')
                for name, args in [('peer_ack', {'message_id': message}), ('task_groups', {})]:
                    await self.mcp(session, name, args, error='CALLER_NOT_READY')
        environment = self.environment()
        environment['NPEER_BOOTSTRAP'] = str(self.binding)
        for env in (environment, self.environment()):
            async with self.session(native=True, environment=env) as session:
                for name, args in [('peer_ack', {'message_id': message}), ('task_groups', {})]:
                    await self.mcp(session, name, args, error='CALLER_NOT_READY')
        self.assertEqual(self.snapshot(), before)

    async def test_bound_calls_revalidate_lease_incarnation_and_actor_scope(self):
        message = self.message()
        self.group()
        before = self.snapshot()
        cases = [
            (dict(self.lease, expires_at=0), 'CALLER_UNBOUND'),
            (dict(self.lease, owner=dict(self.owner, start='stale')), 'CALLER_UNBOUND'),
            (dict(self.lease, incarnation_id=new_id()), 'TARGET_REBOUND'),
        ]
        async with self.session() as session:
            for lease, code in cases:
                atomic_json(self.binding, lease)
                for name, args in [('peer_ack', {'message_id': message}), ('task_groups', {})]:
                    await self.mcp(session, name, args, error=code)
            atomic_json(self.binding, dict(self.lease, kind='controller', group_id='controller-group', generation=1))
            await self.mcp(session, 'peer_ack', {'message_id': message}, error='APPROVAL_BLOCKED')
            await self.mcp(session, 'task_groups', {}, error='CALLER_UNBOUND')
            atomic_json(self.binding, self.lease)
            self.journal.db.execute("UPDATE peer_instances SET status='replaced'")
            offline = self.snapshot()
            for name, args in [('peer_ack', {'message_id': message}), ('task_groups', {})]:
                await self.mcp(session, name, args, error='TARGET_OFFLINE')
            self.assertEqual(self.snapshot(), offline)
            self.journal.db.execute("UPDATE peer_instances SET status='live'")
        self.assertEqual(self.snapshot(), before)
        for caller in (Caller('external-local', 'local'), Caller('controller', 'controller')):
            for name, args, code in [('peer_ack', {'message_id': message}, 'APPROVAL_BLOCKED'),
                                     ('task_groups', {}, 'CALLER_UNBOUND')]:
                with self.subTest(kind=caller.kind, name=name), self.assertRaises(PeerError) as raised:
                    await self.core.tool(caller, name, args)
                self.assertEqual(raised.exception.code, code)

    async def test_bound_preflight_and_exact_resume_share_cli_core_without_replay(self):
        from nativepeers.compatibility import fingerprint
        from nativepeers.files import canonical,sha256
        home=self.root/'home';binary=home/'.local/bin/claude';binary.parent.mkdir(parents=True);binary.write_text('never execute metadata fixture')
        atomic_json(self.journal.root/'compat/runtime.json',{'providers':{'claude':fingerprint(binary)}})
        self.lease['grants']={'can_spawn':True,'max_depth':1,'max_concurrent_workers':2};atomic_json(self.binding,self.lease)
        request={'workers':[{'provider':'claude','model':'sonnet','title':'check only','task':'inspect fixture'}],'limits':{'max_concurrent_workers':1}}
        before=self.snapshot()
        async with self.session() as session:
            names=[v.name for v in (await session.list_tools()).tools];self.assertIn('worker_resume',names)
            planned=await self.mcp(session,'worker_spawn',{'spec':request,'check':True})
            self.assertTrue(planned['read_only']);self.assertTrue(planned['ready']);self.assertEqual(self.snapshot(),before)
            spec=self.root/'batch.json';spec.write_text(json.dumps(request))
            cli=await self.cli('spawn','--check','--spec',str(spec))
            for key in ('baseline','native_helpers','ready','read_only','capacity'):self.assertEqual(planned[key],cli[key])
            group=self.group();worker=self.journal.one('SELECT worker_id FROM workers WHERE group_id=?',(group,))['worker_id']
            task=self.journal.one('SELECT task_id FROM tasks WHERE worker_id=?',(worker,))['task_id']
            response={'task_id':task,'worker_id':worker,'state':'QUEUED'}
            self.journal.db.execute('INSERT INTO resume_requests VALUES(?,?,?,?,?)',(self.caller.key,'exact-resume',sha256({'worker_id':worker,'text':'retained input','completion_mode':'wait'}),task,canonical(response).decode()))
            before=self.snapshot();args={'worker_id':worker,'task':'retained input','completion_mode':'wait','client_request_id':'exact-resume'}
            first=await self.mcp(session,'worker_resume',args);self.assertEqual(first,dict(response,state='COMPLETED'))
            self.assertEqual(await self.mcp(session,'worker_resume',args),first);self.assertEqual(self.snapshot(),before)
            await self.mcp(session,'worker_resume',dict(args,task='changed'),error='IDEMPOTENCY_CONFLICT')
            other=self.group(parent='different-parent');other_worker=self.journal.one('SELECT worker_id FROM workers WHERE group_id=?',(other,))['worker_id']
            await self.mcp(session,'worker_resume',dict(args,worker_id=other_worker),error='APPROVAL_BLOCKED')
            await self.mcp(session,'worker_resume',{'worker_id':worker,'task':'missing ID'},error='PROTOCOL_ERROR')
        self.lease.update(state='RESERVED_UNBOUND',provider='claude');atomic_json(self.binding,self.lease)
        async with self.session() as session:
            await self.mcp(session,'worker_resume',args,error='CALLER_NOT_READY')
            await self.mcp(session,'worker_spawn',{'spec':request,'check':True},error='CALLER_NOT_READY')
