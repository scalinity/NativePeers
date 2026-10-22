"""Spawn check shares normalization but cannot reserve, evict, copy or launch work."""
import json
import os
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock,patch

from nativepeers.caller import Caller
from nativepeers.cli import dispatch,parser
from nativepeers.compatibility import fingerprint
from nativepeers.delegation import Delegation
from nativepeers.errors import PeerError
from nativepeers.files import atomic_json
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.registry import Registry
from nativepeers.worktrees import git


class SpawnCheckTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve();self.home=self.root/'home';self.source=self.root/'source';self.source.mkdir()
        (self.source/'main.py').write_text('VALUE=1\n')
        binary=self.home/'.local/bin/claude';binary.parent.mkdir(parents=True);binary.write_text('metadata fixture only')
        (self.home/'.claude').mkdir();(self.home/'.claude/settings.json').write_text('{"model":"sonnet"}')
        self.j=Journal(self.root/'state');self.addCleanup(self.j.close);self.registry=Registry(self.j)
        atomic_json(self.j.root/'compat/runtime.json',{'providers':{'claude':fingerprint(binary)}})
        parent=self.registry.register('claude',self.home/'.claude',new_id(),'check parent',identity(os.getpid()).json(),str(self.source),{},owner_kind='test',policy={'profiles':['analysis','workspace']})
        self.caller=Caller('peer',parent['handle'],parent['incarnation_id']);self.delegation=Delegation(self.j,self.registry)
        self.enterContext(patch('nativepeers.delegation.Path.home',return_value=self.home))
        self.enterContext(patch.dict(os.environ,{'CLAUDE_CONFIG_DIR':str(self.home/'.claude')}))
        self.start=self.enterContext(patch.object(Delegation,'ensure_supervisor',new_callable=AsyncMock))
        popen=subprocess.Popen
        def non_model_only(argv,*args,**kwargs):
            if Path(argv[0]).name not in ('git','ps'):raise AssertionError('No provider or supervisor process permitted')
            return popen(argv,*args,**kwargs)
        self.enterContext(patch('subprocess.Popen',side_effect=non_model_only))

    def request(self):return {'client_request_id':'same-check-and-spawn','workers':[{'provider':'claude','title':'check worker','task':'focused fixture'}]}

    async def test_quota_preflight_is_read_only_and_exact_dispatch_respects_override(self):
        from nativepeers.usage import store
        from nativepeers.usage.normalize import pool
        from nativepeers.worker_process import Worker
        from unittest.mock import Mock
        store.record(self.j,'claude',self.home/'.claude',dict(source='fixture-native',pools=[pool('weekly',used=100,models=['*'])],models=[]))
        before=self.j.all('SELECT * FROM usage_observations');request=self.request()
        plan=await self.delegation.spawn(self.caller,request,check=True)
        self.assertFalse(plan['ready']);self.assertEqual(plan['quota_blocked'][0]['state'],'EXHAUSTED')
        self.assertEqual(self.j.all('SELECT * FROM usage_observations'),before)
        admitted=await self.delegation.spawn(self.caller,request)
        worker=Worker(self.j,admitted['workers'][0]['worker_id']);worker.emit=Mock();worker.bootstrap()
        worker.claude_turn=AsyncMock(return_value={'status':'completed','text':'exact fake result','native_turn_ids':[]})
        task=self.j.one('SELECT task_id FROM tasks WHERE worker_id=?',(worker.row['worker_id'],))['task_id']
        await worker.task(task,'fixture');worker.claude_turn.assert_not_awaited()
        record=self.delegation.result(self.caller,admitted['group_id'])['results'][0]
        self.assertEqual(record['error']['code'],'QUOTA_EXHAUSTED');self.assertFalse(record['error']['may_have_received'])
        retried=await self.delegation.spawn(self.caller,request)
        self.assertEqual(retried['group_id'],admitted['group_id'])
        request['client_request_id']='explicit-override';request['workers'][0]['quota_policy']='warn'
        # Inline fake controllers share the test parent's PID. Their normal
        # owner cleanup also marks this fixture-only parent row offline.
        self.j.db.execute("UPDATE peer_instances SET status='live' WHERE peer_key=?",(self.caller.key,))
        override=await self.delegation.spawn(self.caller,request)
        worker=Worker(self.j,override['workers'][0]['worker_id']);worker.emit=Mock();worker.bootstrap()
        worker.claude_turn=AsyncMock(return_value={'status':'completed','text':'exact fake result','native_turn_ids':[]})
        task=self.j.one('SELECT task_id FROM tasks WHERE worker_id=?',(worker.row['worker_id'],))['task_id']
        await worker.task(task,'fixture');worker.claude_turn.assert_awaited_once()
        self.assertEqual(worker.spec['model'],'sonnet')

    async def test_shared_pool_advisory_and_atomic_explicit_local_ceiling(self):
        import asyncio
        from nativepeers.usage import store
        from nativepeers.usage.normalize import pool
        store.record(self.j,'claude',self.home/'.claude',dict(source='fixture-native',pools=[pool('shared',used=50,models=['*'])],models=[]))
        request=self.request();request['workers']=[dict(request['workers'][0],title='worker '+str(n)) for n in range(3)]
        plan=await self.delegation.spawn(self.caller,request,check=True)
        self.assertTrue(plan['ready']);self.assertEqual(plan['usage']['shared_pools'][0]['proposed_workers'],3)
        atomic_json(self.j.root/'policy.json',{'usage':{'pool_concurrency':{'claude:shared':2}}})
        plan=await self.delegation.spawn(self.caller,request,check=True)
        self.assertFalse(plan['ready']);self.assertTrue(plan['pool_concurrency_blocked'])
        request['workers']=request['workers'][:2]
        a=dict(request,client_request_id='concurrent-a');b=dict(request,client_request_id='concurrent-b')
        results=await asyncio.gather(self.delegation.spawn(self.caller,a),self.delegation.spawn(self.caller,b),return_exceptions=True)
        self.assertEqual(sum(isinstance(r,dict) for r in results),1)
        self.assertEqual([r.code for r in results if isinstance(r,PeerError)],['CAPACITY_UNAVAILABLE'])
        self.assertEqual(self.j.one('SELECT COUNT(*) n FROM workers')['n'],2)

    def rows(self):return {table:self.j.all('SELECT * FROM '+table+' ORDER BY rowid') for table in ('groups','workers','tasks','completion_routes','spawn_requests','events','artifacts')}

    async def test_public_check_matches_actual_spawn_without_reservations_or_files(self):
        request=self.request();spec=self.root/'spec.json';spec.write_text(json.dumps(request))
        before=self.rows();source=(self.source/'main.py').read_bytes()
        with patch('nativepeers.cli.bind_caller',return_value=self.caller):
            plan=await dispatch(parser().parse_args(['spawn','--check','--spec',str(spec)]),self.j)
        self.assertTrue(plan['ready']);self.assertTrue(plan['read_only']);self.assertEqual(plan['native_helpers'],[])
        self.assertEqual(self.rows(),before);self.assertFalse((self.source/'.worktrees').exists());self.start.assert_not_awaited()
        actual=await self.delegation.spawn(self.caller,request)
        worker=self.j.one('SELECT * FROM workers');lease=json.loads(worker['lease_json']);normalized=plan['workers'][0]
        self.assertEqual(actual['client_request_id'],plan['client_request_id'])
        for key in ('provider','title','task','model','permission_profile','max_task_turns','wall_time_seconds','cwd'):
            self.assertEqual(normalized[key],lease['spec'][key])
        self.assertEqual(normalized['provider_home'],lease['provider_home']);self.assertEqual(normalized['executable'],lease['executable'])
        self.assertEqual((self.source/'main.py').read_bytes(),source);self.start.assert_awaited_once()
        before=self.rows();again=await self.delegation.spawn(self.caller,request,check=True)
        self.assertEqual(again['existing_group_id'],actual['group_id']);self.assertEqual(self.rows(),before)

    async def test_check_capture_of_selected_copy_has_no_materialization(self):
        request=self.request();request['workers'][0].update(permission_profile='workspace',workspace={'mode':'isolated-copy','file_allowlist':['main.py']})
        before=self.rows();plan=await self.delegation.spawn(self.caller,request,check=True)
        self.assertTrue(plan['ready']);self.assertIn('main.py',plan['baseline']['copy_inputs'])
        self.assertEqual(self.rows(),before);self.assertFalse((self.source/'.worktrees').exists());self.start.assert_not_awaited()

    async def test_git_edit_plan_preserves_source_index_and_selected_files(self):
        git(self.source,'init','-q');git(self.source,'add','main.py');git(self.source,'commit','-qm','Create spawn check fixture')
        (self.source/'main.py').write_text('VALUE=2\n');(self.source/'selected.txt').write_text('selected input')
        git(self.source,'status','--porcelain')
        before_files={str(path.relative_to(self.source)):path.read_bytes() for path in self.source.rglob('*') if path.is_file()}
        request=self.request();request['workers'][0].update(permission_profile='workspace',workspace={'mode':'isolated-worktree','untracked_allowlist':['selected.txt']})
        before=self.rows();plan=await self.delegation.spawn(self.caller,request,check=True)
        self.assertTrue(plan['ready']);self.assertTrue(plan['baseline']['git']);self.assertIn('selected.txt',plan['baseline']['untracked_inputs'])
        self.assertEqual(self.rows(),before);self.assertFalse((self.source/'.worktrees').exists())
        self.assertEqual({str(path.relative_to(self.source)):path.read_bytes() for path in self.source.rglob('*') if path.is_file()},before_files)
        self.start.assert_not_awaited()

    async def test_drift_is_explained_without_repair_or_reservation(self):
        (self.home/'.local/bin/claude').write_text('different fixture binary')
        before=self.rows();plan=await self.delegation.spawn(self.caller,self.request(),check=True)
        self.assertFalse(plan['ready']);self.assertEqual(plan['failures'][0]['error']['code'],'CAPABILITY_MISMATCH')
        self.assertEqual(self.rows(),before);self.start.assert_not_awaited()

    async def test_invalid_profile_and_copy_selection_fail_without_mutation(self):
        for profile,workspace,code in [('trusted',{'mode':'inherit-read-only'},'PROTOCOL_ERROR'),('workspace',{'mode':'isolated-copy','file_allowlist':['../outside']},'WORKSPACE_SNAPSHOT_REQUIRED')]:
            with self.subTest(profile=profile):
                request=self.request();request['workers'][0].update(permission_profile=profile,workspace=workspace)
                before=self.rows()
                with self.assertRaises(PeerError) as error:await self.delegation.spawn(self.caller,request,check=True)
                self.assertEqual(error.exception.code,code);self.assertEqual(self.rows(),before)
        self.start.assert_not_awaited()

    async def test_codex_catalog_helper_is_reported_and_invalid_model_is_not_planned(self):
        binary=self.home/'.local/share/codex-mcp-manager/current/bin/codex';binary.parent.mkdir(parents=True);binary.write_text('catalog fixture')
        (self.home/'.codex').mkdir();(self.home/'.codex/config.toml').write_text('model="exact-fixture"\nmodel_reasoning_effort="xhigh"\n')
        providers=json.loads((self.j.root/'compat/runtime.json').read_text())['providers'];providers['codex']=fingerprint(binary);atomic_json(self.j.root/'compat/runtime.json',{'providers':providers})
        client=AsyncMock();client.call.return_value={'data':[{'model':'exact-fixture','supportedReasoningEfforts':[{'reasoningEffort':'xhigh'}]}]}
        request=self.request();request['workers'][0]['provider']='codex';before=self.rows()
        with patch.dict(os.environ,{'CODEX_HOME':str(self.home/'.codex')}),patch('nativepeers.delegation.AppServer',return_value=client):
            plan=await self.delegation.spawn(self.caller,request,check=True)
            self.assertTrue(plan['ready']);self.assertEqual(plan['workers'][0]['model'],'exact-fixture')
            self.assertEqual(plan['native_helpers'],[{'provider':'codex','operation':'model/list','model_input':False}])
            self.assertEqual(client.call.await_args.args[0],'model/list');client.close.assert_awaited_once()
            request['workers'][0]['model']='unavailable'
            with self.assertRaises(PeerError) as error:await self.delegation.spawn(self.caller,request,check=True)
            self.assertEqual(error.exception.code,'MODEL_UNAVAILABLE')
        self.assertEqual(self.rows(),before);self.start.assert_not_awaited()

    async def test_public_cold_index_check_does_not_refresh_index_or_staged_inputs(self):
        git(self.source,'init','-q');git(self.source,'add','main.py');git(self.source,'commit','-qm','Create cold index fixture')
        (self.source/'staged.txt').write_text('staged input');git(self.source,'add','staged.txt')
        path=self.source/'main.py';info=path.stat();os.utime(path,ns=(info.st_atime_ns,info.st_mtime_ns+2000000000))
        index=self.source/'.git/index';before=index.read_bytes();rows=self.rows()
        request=self.request();request['workers'][0].update(permission_profile='workspace',workspace={'mode':'isolated-worktree'})
        spec=self.root/'cold.json';spec.write_text(json.dumps(request))
        with patch('nativepeers.cli.bind_caller',return_value=self.caller):
            value=await dispatch(parser().parse_args(['spawn','--check','--spec',str(spec)]),self.j)
        self.assertTrue(value['ready']);self.assertEqual(index.read_bytes(),before)
        self.assertEqual(path.read_text(),'VALUE=1\n');self.assertEqual(self.rows(),rows)
        self.assertFalse((self.source/'.worktrees').exists());self.start.assert_not_awaited()

    async def test_public_check_refuses_custom_normalization_without_running_filter(self):
        (self.source/'.gitattributes').write_text('main.py filter=fixture\n')
        git(self.source,'init','-q');git(self.source,'add','.');git(self.source,'commit','-qm','Create filter fixture')
        marker=self.root/'filter-ran';git(self.source,'config','filter.fixture.clean','touch '+str(marker)+'; cat')
        (self.source/'main.py').write_text('VALUE=2\n');before=(self.source/'.git/index').read_bytes();rows=self.rows()
        request=self.request();spec=self.root/'filter.json';spec.write_text(json.dumps(request))
        with patch('nativepeers.cli.bind_caller',return_value=self.caller),self.assertRaises(PeerError) as error:
            await dispatch(parser().parse_args(['spawn','--check','--spec',str(spec)]),self.j)
        self.assertIn('Custom Git filter',error.exception.message)
        self.assertFalse(marker.exists());self.assertEqual((self.source/'.git/index').read_bytes(),before)
        self.assertEqual(self.rows(),rows);self.start.assert_not_awaited()

    async def test_builtin_line_ending_normalization_still_plans(self):
        (self.source/'.gitattributes').write_text('main.py text eol=crlf\n')
        git(self.source,'init','-q');git(self.source,'add','.');git(self.source,'commit','-qm','Create normalization fixture')
        (self.source/'main.py').write_bytes(b'VALUE=1\r\n')
        request=self.request();request['workers'][0].update(permission_profile='workspace',workspace={'mode':'isolated-worktree'})
        plan=await self.delegation.spawn(self.caller,request,check=True)
        self.assertTrue(plan['ready']);self.assertFalse((self.source/'.worktrees').exists())

    async def test_git_selection_excludes_offline_registered_home_before_materialization(self):
        git(self.source,'init','-q');git(self.source,'add','main.py');git(self.source,'commit','-qm','Create private input fixture')
        private=self.source/'provider-cache';private.mkdir();(private/'settings.json').write_text('dummy private-state fixture')
        (self.source/'neighbor.txt').write_text('ordinary selected input')
        peer=self.registry.register('codex',private,new_id(),'offline home',identity(os.getpid()).json(),str(private),{},owner_kind='test')
        self.j.db.execute("UPDATE peer_instances SET status='offline' WHERE peer_key=?",(peer['handle'],))
        # This fake historical registration shares the fixture PID; keep the real fixture parent live.
        self.j.db.execute("UPDATE peer_instances SET status='live' WHERE peer_key=?",(self.caller.key,))
        request=self.request();request['workers'][0].update(permission_profile='workspace',workspace={'mode':'isolated-worktree','untracked_allowlist':['provider-cache/settings.json']})
        for check in (True,False):
            with self.subTest(check=check),self.assertRaises(PeerError) as error:await self.delegation.spawn(self.caller,request,check=check)
            self.assertEqual(error.exception.code,'WORKSPACE_SNAPSHOT_REQUIRED')
            self.assertFalse((self.source/'.worktrees').exists());self.start.assert_not_awaited()
        request['client_request_id']='safe-git-neighbor';request['workers'][0]['workspace']['untracked_allowlist']=['neighbor.txt']
        planned=await self.delegation.spawn(self.caller,request,check=True);self.assertTrue(planned['ready'])
        self.assertIn('neighbor.txt',planned['baseline']['untracked_inputs'])

    async def test_git_revalidation_rechecks_new_registered_homes_in_executor(self):
        import asyncio
        from nativepeers.worktrees import freeze,validate_frozen
        git(self.source,'init','-q');git(self.source,'add','main.py');git(self.source,'commit','-qm','Create revalidation fixture')
        private=self.source/'late-home';private.mkdir();(private/'settings.json').write_text('dummy')
        frozen=await asyncio.to_thread(freeze,self.source,[{'mode':'isolated-worktree','untracked_allowlist':['late-home/settings.json']}],journal=self.j)
        peer=self.registry.register('codex',private,new_id(),'new private home',identity(os.getpid()).json(),str(private),{},owner_kind='test')
        self.j.db.execute("UPDATE peer_instances SET status='offline' WHERE peer_key=?",(peer['handle'],))
        with self.assertRaises(PeerError):await asyncio.to_thread(validate_frozen,self.source,frozen)
        self.assertFalse((self.source/'.worktrees').exists())
