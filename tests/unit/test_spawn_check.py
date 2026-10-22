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
