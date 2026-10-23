import asyncio
import json
import os
from pathlib import Path
import shlex
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from nativepeers.caller import Caller
from nativepeers.delegation import Delegation
from nativepeers.errors import PeerError
from nativepeers.files import canonical
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.profiles import claude_arguments
from nativepeers.recovery import resume_command, resume_worker
from nativepeers.registry import Registry
from nativepeers.worker_process import Worker


class WorkerModelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name).resolve()
        for provider in ('claude', 'codex'):
            (self.home / ('.' + provider)).mkdir()
        environment = patch.dict(os.environ, {'CODEX_HOME': str(self.home / '.codex'),
                                              'CLAUDE_CONFIG_DIR': str(self.home / '.claude')})
        environment.start()
        self.addCleanup(environment.stop)
        (self.home / '.claude/settings.json').write_text('{"model":"sonnet"}')
        (self.home / '.codex/config.toml').write_text('model="gpt-fixture"\nmodel_reasoning_effort="high"\n')
        for relative in ('.local/bin/claude', '.local/share/codex-mcp-manager/current/bin/codex'):
            path = self.home / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture only')
        self.journal = Journal(self.home / 'state')
        self.addCleanup(self.journal.close)
        self.registry = Registry(self.journal)
        parent = self.registry.register('codex', self.home, new_id(), 'parent',
                                       identity(os.getpid()).json(), self.home, {'kind': 'fake'})
        self.caller = Caller('peer', parent['handle'], parent['incarnation_id'])
        self.delegation = Delegation(self.journal, self.registry)

    async def spawn(self, request):
        client = AsyncMock()
        client.call.return_value = {'data': [{'model': 'gpt-fixture',
                                             'supportedReasoningEfforts': [{'reasoningEffort': 'high'}]}]}
        with patch('nativepeers.delegation.Path.home', return_value=self.home), \
             patch('nativepeers.delegation.AppServer', return_value=client), \
             patch.object(self.delegation, 'ensure_supervisor', new_callable=AsyncMock):
            return await self.delegation.spawn(self.caller, request)

    async def test_both_providers_keep_explicit_models_and_idempotent_defaults(self):
        request = {'client_request_id': 'models-1', 'workers': [
            {'provider': 'claude', 'title': 'review', 'task': 'fixture', 'model': 'opus'},
            {'provider': 'codex', 'title': 'implement', 'task': 'fixture', 'model': 'gpt-fixture'},
            {'provider': 'claude', 'title': 'default', 'task': 'fixture'},
        ]}
        first = await self.spawn(request)
        rows = self.journal.all('SELECT model_requested,lease_json FROM workers ORDER BY rowid')
        self.assertEqual([r['model_requested'] for r in rows], ['opus', 'gpt-fixture', 'sonnet'])
        self.assertEqual([json.loads(r['lease_json'])['spec']['model'] for r in rows],
                         ['opus', 'gpt-fixture', 'sonnet'])
        leases = [json.loads(r['lease_json']) for r in rows]
        self.assertEqual([lease['provider_home'] for lease in leases],
                         [str(self.home / name) for name in ('.claude', '.codex', '.claude')])
        self.assertEqual(leases[1]['spec']['reasoning_effort'], 'high')
        (self.home / '.claude/settings.json').write_text('{"model":"haiku"}')
        second = await self.spawn(request)
        self.assertEqual(first['group_id'], second['group_id'])
        self.assertEqual(self.journal.one('SELECT COUNT(*) AS n FROM workers')['n'], 3)

    async def test_alternate_homes_pin_defaults_profiles_identity_and_both_resumes(self):
        from nativepeers.profiles import codex_config
        from nativepeers.recovery import resume_worker
        from nativepeers.launch import home_environment
        homes={provider:self.home/('alternate '+provider) for provider in ('claude','codex')}
        for home in homes.values():home.mkdir()
        (homes['claude']/'settings.json').write_text('{"model":"opus","enabledPlugins":{"alternate-plugin":true}}')
        (homes['codex']/'config.toml').write_text('model="gpt-fixture"\nmodel_reasoning_effort="high"\n[mcp_servers.alternate-server]\ncommand="fixture"\n[plugins.alternate-plugin]\nenabled=true\n')
        request={'workers':[{'provider':provider,'title':provider,'task':'fixture'} for provider in ('claude','codex')]}
        with patch.dict(os.environ,{'CLAUDE_CONFIG_DIR':str(homes['claude']),'CODEX_HOME':str(homes['codex'])}):
            result=await self.spawn(request)
        rows=self.journal.all('SELECT * FROM workers ORDER BY rowid')
        self.assertEqual([r['model_requested'] for r in rows],['opus','gpt-fixture'])
        for row in rows:
            provider=row['provider'];lease=json.loads(row['lease_json']);home=homes[provider]
            self.assertEqual(lease['provider_home'],str(home))
            with patch.dict(os.environ,{'CLAUDE_CONFIG_DIR':str(self.home/'wrong'),'CODEX_HOME':str(self.home/'wrong')}):
                worker=Worker(self.journal,row['worker_id']);self.assertEqual(worker.home,home)
                worker.bootstrap();native=worker.native or new_id();worker.bind(native)
                key=self.journal.one('SELECT peer_key FROM workers WHERE worker_id=?',(row['worker_id'],))['peer_key']
                worker.bootstrap();worker.bind(native)
                self.assertEqual(self.journal.one('SELECT peer_key FROM workers WHERE worker_id=?',(row['worker_id'],))['peer_key'],key)
                if provider=='claude':
                    args=claude_arguments(self.journal,worker.binding,worker.spec,native,home=worker.home)
                    settings=json.loads(args[args.index('--settings')+1]);self.assertEqual(settings['enabledPlugins'],{'alternate-plugin':False})
                else:
                    config=codex_config(self.journal,worker.binding,'analysis',home=worker.home)
                    self.assertIn('alternate-server',config['mcp_server_policy']);self.assertIn('alternate-plugin',config['plugins'])
                with patch('nativepeers.recovery.assert_released'):
                    command=resume_command(self.journal,self.caller,row['worker_id'])
                self.assertEqual(command['native_env'],home_environment(provider,home));self.assertIn(str(home),command['command'])
                self.journal.db.execute("UPDATE workers SET state='RELEASED_RESUMABLE' WHERE worker_id=?",(row['worker_id'],))
                parent={'owner':identity(os.getpid()).json(),'incarnation_id':self.caller.incarnation,'policy':{'profiles':['analysis']}}
                with patch('nativepeers.recovery.assert_released'),patch('nativepeers.recovery.Registry.verify',return_value=parent),patch('nativepeers.recovery.Delegation.ensure_supervisor',new_callable=AsyncMock):
                    await resume_worker(self.journal,self.caller,row['worker_id'],'followup','wait')
                retained=json.loads(self.journal.one('SELECT lease_json FROM workers WHERE worker_id=?',(row['worker_id'],))['lease_json'])
                self.assertEqual(retained['provider_home'],str(home));self.assertEqual(retained['spec']['model'],row['model_requested'])

    async def test_explicit_blank_models_are_rejected_instead_of_using_a_default(self):
        for provider in ('claude', 'codex','antigravity'):
            for model in ('', ' ', '\n'):
                with self.subTest(provider=provider, model=repr(model)):
                    with self.assertRaises(PeerError) as error:
                        await self.spawn({'workers': [{'provider': provider, 'title': 'bad',
                                                      'task': 'fixture', 'model': model}]})
                    self.assertEqual(error.exception.code, 'PROTOCOL_ERROR')
        self.assertEqual(self.journal.one('SELECT COUNT(*) AS n FROM workers')['n'], 0)

    def test_manual_resume_keeps_model_and_codex_effort(self):
        for provider, selected in [('claude', 'claude-opus-5-5'), ('codex', 'gpt-fixture')]:
            row = {'provider': provider, 'native_id': new_id(), 'model_requested': 'opus',
                   'model_observed': selected, 'effort_requested': 'xhigh','cwd':str(self.home/'isolated worktree')}
            with self.subTest(provider=provider), \
                 patch('nativepeers.recovery.owned_worker', return_value=(row, {})), \
                 patch('nativepeers.recovery.assert_released'):
                args = shlex.split(resume_command(self.journal, self.caller, 'worker')['command'])
                self.assertEqual(args[:3],['cd',row['cwd'],'&&'])
                self.assertEqual(args[args.index('--model') + 1], selected)
                if provider == 'codex':
                    self.assertIn('model_reasoning_effort="xhigh"', args)
                else:self.assertEqual(args[args.index('--effort')+1],'xhigh')

    async def worker(self, requested='opus'):
        group, worker, native = new_id(), new_id(), new_id()
        spec = {'provider': 'claude', 'title': 'selected worker', 'model': requested,
                'cwd': str(self.home), 'permission_profile': 'analysis'}
        lease = {'spec': spec, 'grants': {'worker_id': worker, 'group_id': group},
                 'binding': str(self.journal.root / 'bindings' / new_id()),
                 'parent_key': self.caller.key, 'expires_at': time.time() + 60}
        self.journal.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',
                                (group, self.caller.key, '{}', 'RESERVED', '{}'))
        self.journal.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                (worker, group, None, 'claude', native, spec['title'], requested,
                                 None, None, str(self.home), None, 'RESERVED', 1, canonical(lease).decode()))
        value = Worker(self.journal, worker)
        value.bootstrap()
        return value

    async def test_model_observations_persist_without_rebinding_native_identity(self):
        worker = await self.worker()
        worker.bind(worker.native, model='claude-opus-5-5')
        binding = json.loads(worker.binding.read_text())
        self.assertEqual(binding['worker_id'],worker.row['worker_id'])
        worker.bind(worker.native, model='claude-opus-5-5')
        row = self.journal.one('SELECT * FROM workers WHERE worker_id=?', (worker.row['worker_id'],))
        self.assertEqual(row['model_requested'], 'opus')
        self.assertEqual(json.loads(row['lease_json'])['spec']['model'], 'claude-opus-5-5')
        resumed = Worker(self.journal, worker.row['worker_id'])
        self.assertEqual(resumed.model_observed, 'claude-opus-5-5')
        args = claude_arguments(self.journal, worker.binding, resumed.spec, worker.native, resume=True)
        self.assertEqual(args[args.index('--model') + 1], 'claude-opus-5-5')
        self.assertEqual(json.loads(worker.binding.read_text()), binding)
        with self.assertRaises(PeerError) as error:
            worker.bind(worker.native, model='claude-sonnet-5-5')
        self.assertEqual(error.exception.code, 'MODEL_UNAVAILABLE')

    async def test_task_retains_optional_report_and_raw_final_output(self):
        from unittest.mock import Mock
        worker=await self.worker();task=new_id()
        text='actual final output\n```nativepeers-result\n'+json.dumps({'findings':[{'severity':'info','summary':'scoped finding'}],
            'tests_run':[{'command':'python fixture.py','outcome':'passed'}]})+'\n```'
        self.journal.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(task,worker.row['worker_id'],None,None,'QUEUED',None,None,1,'{}'))
        worker.claude_turn=AsyncMock(return_value={'status':'completed','text':text});worker.emit=Mock()
        await worker.task(task,'fixture')
        self.assertIn('nativepeers-result',worker.claude_turn.call_args.args[0])
        row=self.journal.one('SELECT result_artifact_id FROM tasks WHERE task_id=?',(task,))
        result=json.loads((self.journal.root/'artifacts'/row['result_artifact_id']).read_text())
        self.assertEqual(result['summary'],text);self.assertEqual(result['findings'][0]['summary'],'scoped finding')
        self.assertEqual(result['verification']['tests_run'][0]['outcome'],'passed')
        self.assertFalse(result['verification']['tests_run'][0]['independently_verified'])
        worker.claude_turn.assert_awaited_once()

    async def test_exact_claude_model_cannot_be_silently_substituted(self):
        worker = await self.worker('claude-opus-5-5')
        with self.assertRaises(PeerError) as error:
            worker.bind(worker.native, model='claude-sonnet-5-5')
        self.assertEqual(error.exception.code, 'MODEL_UNAVAILABLE')

    async def test_claude_context_suffix_is_launch_selection_not_model_identity(self):
        for requested in ('claude-opus-5-5[1m]', 'opus[1m]'):
            for echoes in (('claude-opus-5-5[1m]', 'claude-opus-5-5'),
                           ('claude-opus-5-5', 'claude-opus-5-5[1m]')):
                with self.subTest(requested=requested, echoes=echoes):
                    worker = await self.worker(requested)
                    for echo in echoes:
                        worker.bind(worker.native, model=echo)
                        self.assertEqual(worker.model_observed, 'claude-opus-5-5')
                        self.assertEqual(worker.spec['model'], 'claude-opus-5-5[1m]')
                    row = self.journal.one('SELECT * FROM workers WHERE worker_id=?', (worker.row['worker_id'],))
                    self.assertEqual(row['model_requested'], requested)
                    self.assertEqual(row['model_observed'], 'claude-opus-5-5')
                    self.assertEqual(json.loads(row['lease_json'])['spec']['model'], 'claude-opus-5-5[1m]')
                    before = row.copy()
                    with self.assertRaises(PeerError) as error:
                        worker.bind(worker.native, model='claude-sonnet-5-5[1m]')
                    self.assertEqual(error.exception.code, 'MODEL_UNAVAILABLE')
                    self.assertEqual(self.journal.one('SELECT * FROM workers WHERE worker_id=?',
                                                     (worker.row['worker_id'],)), before)

    async def test_claude_serialized_turn_keeps_full_model_and_context_suffix(self):
        for requested in ('claude-opus-5-5[1m]', 'opus[1m]'):
            with self.subTest(requested=requested):
                worker = await self.worker(requested)
                worker.lease['executable'] = 'offline-claude-fixture'
                worker.running_task = new_id()

                def process(*args, **kwargs):
                    stdout, stderr = asyncio.StreamReader(), asyncio.StreamReader()
                    events = [
                        {'type': 'system', 'subtype': 'init', 'session_id': worker.native,
                         'model': 'claude-opus-5-5[1m]', 'tools': ['Read']},
                        {'type': 'assistant', 'session_id': worker.native,
                         'message': {'model': 'claude-opus-5-5'}},
                        {'type': 'result', 'subtype': 'success', 'session_id': worker.native,
                         'result': 'offline fixture', 'num_turns': 1},
                    ]
                    stdout.feed_data(b'\n'.join(canonical(event) for event in events) + b'\n')
                    stdout.feed_eof(); stderr.feed_eof()
                    return SimpleNamespace(pid=os.getpid(), stdin=Mock(drain=AsyncMock()), stdout=stdout,
                                           stderr=stderr, wait=AsyncMock(), returncode=0)

                with patch('nativepeers.worker_process.asyncio.create_subprocess_exec',
                           new_callable=AsyncMock, side_effect=process) as launch, \
                     patch('nativepeers.worker_process.launch_environment', return_value={}):
                    for turn in (1, 2):
                        worker.tasks_started = turn
                        result = await worker.claude_turn('offline fixture')
                        self.assertEqual(result['status'], 'completed')
                first, second = [call.args for call in launch.await_args_list]
                self.assertEqual(first[first.index('--model') + 1], requested)
                self.assertEqual(second[second.index('--model') + 1], 'claude-opus-5-5[1m]')
                self.assertEqual(second[second.index('--resume') + 1], worker.native)

    async def test_claude_context_suffix_survives_manual_automatic_and_legacy_resume(self):
        parent = self.registry.verify(self.caller.key, self.caller.incarnation)
        self.enterContext(patch('nativepeers.registry.Registry.verify', return_value=parent))
        for requested in ('claude-opus-5-5[1m]', 'opus[1m]'):
            for legacy in (False, True):
                with self.subTest(requested=requested, legacy=legacy):
                    spawned = await self.spawn({'workers': [
                        {'provider': 'claude', 'title': 'resume fixture', 'task': 'fixture', 'model': requested}]})
                    row = self.journal.one('SELECT * FROM workers WHERE group_id=?', (spawned['group_id'],))
                    worker = Worker(self.journal, row['worker_id']); worker.bootstrap()
                    worker.bind(worker.native, model='claude-opus-5-5')
                    if legacy:
                        # Previous versions replaced the launch selection with the base echo.
                        worker.spec['model'] = 'claude-opus-5-5'
                        self.journal.db.execute('UPDATE workers SET lease_json=? WHERE worker_id=?',
                                                (canonical(worker.lease).decode(), row['worker_id']))
                    self.journal.db.execute("UPDATE workers SET state='RELEASED_RESUMABLE' WHERE worker_id=?",
                                            (row['worker_id'],))
                    with patch('nativepeers.recovery.assert_released'), \
                         patch('nativepeers.recovery.Delegation.ensure_supervisor', new_callable=AsyncMock):
                        manual = resume_command(self.journal, self.caller, row['worker_id'])['native_argv']
                        await resume_worker(self.journal, self.caller, row['worker_id'], 'fixture', 'wait')
                    self.assertEqual(manual[manual.index('--model') + 1], 'claude-opus-5-5[1m]')
                    resumed = Worker(self.journal, row['worker_id'])
                    self.assertEqual(resumed.spec['model'], 'claude-opus-5-5[1m]')
                    self.assertEqual(resumed.model_observed, 'claude-opus-5-5')
                    with self.assertRaises(PeerError) as error:
                        resumed.bind(worker.native, model='claude-sonnet-5-5')
                    self.assertEqual(error.exception.code, 'MODEL_UNAVAILABLE')

    async def test_first_alias_observation_verifies_family(self):
        for requested, observed, valid in [('sonnet','claude-sonnet-5-5',True),
                ('sonnet[1m]','claude-sonnet-5-5[1m]',True),('sonnet','claude-opus-5-5',False),
                ('sonnet','claude-haiku-5-5',False),('best','claude-fable-5-1',True),
                ('opusplan','claude-sonnet-5-5',True),('unknown','claude-opus-5-5',False)]:
            with self.subTest(requested=requested,observed=observed):
                worker=await self.worker(requested)
                if valid:worker.bind(worker.native,model=observed)
                else:
                    with self.assertRaises(PeerError) as error:worker.bind(worker.native,model=observed)
                    self.assertEqual(error.exception.code,'MODEL_UNAVAILABLE')
                    self.assertIsNone(worker.model_observed)

    async def test_extra_logical_task_retains_definite_budget_failure(self):
        worker=await self.worker();worker.spec['max_task_turns']=1;worker.emit=Mock()
        worker.claude_turn=AsyncMock(return_value={'status':'completed','text':'last allowed task'})
        tasks=[new_id(),new_id()]
        for task in tasks:
            self.journal.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(task,worker.row['worker_id'],None,None,'QUEUED',None,None,1,'{}'))
            await worker.task(task,'fixture')
        row=self.journal.one('SELECT * FROM tasks WHERE task_id=?',(tasks[1],))
        result=json.loads((self.journal.root/'artifacts'/row['result_artifact_id']).read_text())
        self.assertEqual(row['state'],'FAILED');self.assertEqual(result['error']['code'],'BUDGET_EXHAUSTED')
        self.assertFalse(result['error']['may_have_received']);self.assertEqual(result['native_turn_ids'],[])
        worker.claude_turn.assert_awaited_once()
        await worker.task(tasks[1],'exact retry')
        self.assertEqual(self.journal.one('SELECT * FROM tasks WHERE task_id=?',(tasks[1],)),row)
