import asyncio
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from nativepeers.artifacts import Artifacts
from nativepeers.caller import Caller,bind_caller
from nativepeers.cli import dispatch, main, parser
from nativepeers.core import Core
from nativepeers.delegation import Delegation
from nativepeers.errors import PeerError
from nativepeers.files import atomic_json
from nativepeers.journal import Journal
from nativepeers.launch import launch_environment
from nativepeers.models import ARTIFACT_MAX, new_id
from nativepeers.process import identity
from nativepeers.registry import Registry
from nativepeers.provider_errors import classify


class ArtifactsAndAuthority(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.j=Journal(Path(self.temp.name));self.addCleanup(self.j.close)

    def test_distinct_large_result_channel_and_reader_grants(self):
        artifacts=Artifacts(self.j);handle=artifacts.put(b'x'*300000,'owner',['reader'])
        self.assertEqual(artifacts.read(handle,'reader',limit=262144)['bytes_read'],262144)
        self.assertEqual(artifacts.read(handle,'reader',offset=262144)['bytes_read'],37856)
        for who in ['stranger','../../etc/passwd']:
            with self.assertRaises(PeerError):artifacts.read(handle,who)
        with self.assertRaises(PeerError):artifacts.read(handle,'owner',limit=262145)
        with self.assertRaises(PeerError):artifacts.put(b'x'*10485761,'owner')

    def test_tamper_and_symlink_are_rejected(self):
        artifacts=Artifacts(self.j);handle=artifacts.put(b'original','owner');path=self.j.root/'artifacts'/handle
        path.write_bytes(b'changed')
        with self.assertRaises(PeerError):artifacts.read(handle,'owner')
        path.unlink();path.symlink_to('/etc/hosts')
        with self.assertRaises(OSError):artifacts.read(handle,'owner')

    def test_unbound_forged_metadata_never_becomes_human(self):
        with patch.dict(os.environ,{'CODEX_THREAD_ID':'00000000-0000-4000-8000-000000000001'},clear=True):
            with self.assertRaises(PeerError) as error:bind_caller(self.j,Registry(self.j))
            self.assertEqual(error.exception.code,'CALLER_UNBOUND')
        with patch.dict(os.environ,{},clear=True):self.assertEqual(bind_caller(self.j,Registry(self.j)).kind,'external-local')

    def test_private_bootstrap_is_inactive_and_expiry_is_enforced(self):
        binding=self.j.root/'bindings/test';atomic_json(binding,{'state':'RESERVED_UNBOUND'})
        with self.assertRaises(PeerError) as error:bind_caller(self.j,Registry(self.j),fixed_binding=binding)
        self.assertEqual(error.exception.code,'CALLER_NOT_READY')
        atomic_json(binding,{'state':'BOUND','owner':identity(os.getpid()).json(),'expires_at':time.time()-1})
        with self.assertRaises(PeerError):bind_caller(self.j,Registry(self.j),fixed_binding=binding)

    def test_launch_context_drops_parent_bindings_and_keeps_native_home(self):
        with patch.dict(os.environ,{'NPEER_BINDING':'private','CLAUDE_ENV_FILE':'parent-file','CLAUDE_CODE_MESSAGING_TOKEN':'private','CODEX_THREAD_ID':'parent','CODEX_HOME':'/native-home'},clear=True):
            environment=launch_environment('codex');self.assertEqual(environment,{'CODEX_HOME':'/native-home'})
        with patch.dict(os.environ,{'CLAUDECODE':'1'},clear=True):
            with self.assertRaisesRegex(RuntimeError,'nested Claude'):launch_environment('claude')

    def test_provider_failure_taxonomy(self):
        examples={'AUTH_REQUIRED':'authentication failed 401','MODEL_UNAVAILABLE':'model_not_found','EFFORT_UNSUPPORTED':'unsupported effort',
                  'QUOTA_EXHAUSTED':'usage limit reached','RATE_LIMITED':'429 rate limit','APPROVAL_BLOCKED':'permission denied','BUDGET_EXHAUSTED':'error_max_turns'}
        for expected,error in examples.items():self.assertEqual(classify({'error':error}),expected)


class ArtifactAddTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.cwd=self.root/'workspace';self.cwd.mkdir()
        self.home=self.root/'native-home';self.home.mkdir()
        self.j=Journal(self.root/'state');self.addCleanup(self.j.close)
        self.registry=Registry(self.j)
        peer=self.registry.register('codex',self.home,new_id(),'artifact owner',identity(os.getpid()).json(),
                                    self.cwd,{'kind':'fake'},owner_kind='test')
        self.caller=Caller('peer',peer['handle'],peer['incarnation_id'])
        self.artifacts=Artifacts(self.j)
        self.path=self.cwd/'selected.txt';self.path.write_bytes(b'Only the selected context.\n')

    def assert_rejected(self, path, code, **kwargs):
        with self.assertRaises(PeerError) as error:self.artifacts.add_file(path,self.caller,**kwargs)
        self.assertEqual(error.exception.code,code)
        self.assertEqual(self.j.all('SELECT * FROM artifacts'),[])
        self.assertEqual(list((self.j.root/'artifacts').iterdir()),[])

    def test_cli_import_returns_digest_and_keeps_an_immutable_selected_copy(self):
        raw=self.path.read_bytes();(self.cwd/'unselected.txt').write_text('Unselected context')
        args=parser().parse_args(['artifact-add','--file',str(self.path),'--max-bytes',str(len(raw)),'--json'])
        with patch('nativepeers.cli.bind_caller',return_value=self.caller):
            result=asyncio.run(dispatch(args,self.j))
        handle=result['artifact_id'];self.assertTrue(handle.startswith('art_'))
        self.assertEqual(result,dict(artifact_id=handle,sha256=hashlib.sha256(raw).hexdigest(),
                                    byte_size=len(raw),media_type='application/octet-stream'))
        self.path.write_text('Changed source')
        page=self.artifacts.read(handle,self.caller.key)
        self.assertEqual(base64.b64decode(page['data_base64']),raw)
        self.assertEqual(len(self.j.all('SELECT * FROM artifacts')),1)
        self.assertEqual(json.loads(self.j.one('SELECT allowed_readers_json FROM artifacts')['allowed_readers_json']),[])

    def test_context_refs_grant_only_the_selected_worker_and_reject_manufactured_handles(self):
        handle=self.artifacts.add_file(self.path,self.caller)['artifact_id']
        home=self.root/'fixture-home';(home/'.codex').mkdir(parents=True)
        (home/'.codex/config.toml').write_text('model = "fixture-model"\n')
        executable=home/'.local/share/codex-mcp-manager/current/bin/codex'
        executable.parent.mkdir(parents=True);executable.write_text('fixture, never executed')
        client=AsyncMock();client.call.return_value={'data':[{'model':'fixture-model','supportedReasoningEfforts':[{'reasoningEffort':'medium'}]}]}
        delegation=Delegation(self.j,self.registry)
        request={'workers':[{'provider':'codex','title':'selected','task':'Read selected context','context_refs':[handle]},
                            {'provider':'codex','title':'unrelated','task':'No context reference'}]}
        with patch('nativepeers.delegation.Path.home',return_value=home),\
             patch('nativepeers.delegation.AppServer',return_value=client),\
             patch.object(delegation,'ensure_supervisor',new_callable=AsyncMock) as start:
            result=asyncio.run(delegation.spawn(self.caller,request))
            start.assert_awaited_once()
            workers={w['role_title']:w['worker_id'] for w in result['workers']}
            allowed=Caller('peer','selected-native-peer',grants={'worker_id':workers['selected']})
            denied=Caller('peer','unrelated-native-peer',grants={'worker_id':workers['unrelated']})
            page=asyncio.run(Core(self.j).tool(allowed,'artifact_read',{'handle':handle}))
            self.assertEqual(base64.b64decode(page['data_base64']),self.path.read_bytes())
            with self.assertRaises(PeerError) as error:asyncio.run(Core(self.j).tool(denied,'artifact_read',{'handle':handle}))
            self.assertEqual(error.exception.code,'APPROVAL_BLOCKED')
            for manufactured in ('art_'+new_id(),'art_../../etc/passwd'):
                bad={'workers':[dict(request['workers'][0],context_refs=[manufactured])]}
                with self.subTest(handle=manufactured),self.assertRaises(PeerError) as error:
                    asyncio.run(delegation.spawn(self.caller,bad))
                self.assertEqual(error.exception.code,'PROTOCOL_ERROR')
            self.assertEqual(len(self.j.all('SELECT * FROM groups')),1)
        self.assertEqual(json.loads(self.j.one('SELECT allowed_readers_json FROM artifacts')['allowed_readers_json']),[workers['selected']])

    def test_unbound_and_manufactured_callers_cannot_import(self):
        for caller in (Caller('external-local','external-local'),Caller('controller','controller:fixture'),
                       Caller('peer',self.caller.key),Caller('peer','np1_'+'0'*64,new_id()),
                       Caller('peer',self.caller.key,new_id())):
            with self.subTest(caller=caller),self.assertRaises(PeerError):self.artifacts.add_file(self.path,caller)
        with patch.dict(os.environ,{},clear=True),patch('nativepeers.caller.ancestry',return_value=[]):
            args=parser().parse_args(['artifact-add','--file',str(self.path)])
            with self.assertRaises(PeerError) as error:asyncio.run(dispatch(args,self.j))
            self.assertEqual(error.exception.code,'CALLER_UNBOUND')
        with patch('nativepeers.cli.bind_caller',side_effect=PeerError('CALLER_UNBOUND','Not bound')):
            with self.assertRaises(PeerError):asyncio.run(dispatch(args,self.j))
        self.assertEqual(self.j.all('SELECT * FROM artifacts'),[])

    def test_worker_import_is_confined_to_registered_cwd_even_after_chdir(self):
        worker=self.registry.register('codex',self.home,new_id(),'worker',identity(os.getpid()).json(),self.cwd,
                                      {'kind':'owned-worker'},owner_kind='owned-worker-controller',policy={'worker_id':'worker-fixture'})
        self.caller=Caller('peer',worker['handle'],worker['incarnation_id'],{'worker_id':'worker-fixture'})
        outside=self.root/'outside.txt';outside.write_text('Outside the owned cwd')
        self.assert_rejected(outside,'APPROVAL_BLOCKED')
        sibling=self.root/'workspace-other';sibling.mkdir();(sibling/'context.txt').write_text('Outside')
        self.assert_rejected(sibling/'context.txt','APPROVAL_BLOCKED')
        with patch('pathlib.Path.cwd',return_value=self.root):self.assert_rejected(Path('outside.txt'),'APPROVAL_BLOCKED')
        link=self.cwd/'shortcut';link.symlink_to(self.root,target_is_directory=True)
        self.assert_rejected(link/'outside.txt','PROTOCOL_ERROR')
        self.assert_rejected(self.cwd/'..'/'outside.txt','PROTOCOL_ERROR')
        self.assertTrue(self.artifacts.add_file(self.path,self.caller)['artifact_id'].startswith('art_'))

    def test_missing_directory_fifo_and_symlink_inputs_are_rejected(self):
        fifo=self.cwd/'pipe';os.mkfifo(fifo)
        link=self.cwd/'link.txt';link.symlink_to(self.path)
        directory_link=self.cwd/'directory-link';directory_link.symlink_to(self.cwd,target_is_directory=True)
        for path in (self.cwd/'missing',self.cwd,fifo,link,directory_link/'selected.txt'):
            with self.subTest(path=path):self.assert_rejected(path,'PROTOCOL_ERROR')

    def test_secret_paths_native_state_and_transcripts_are_rejected(self):
        for relative in ('.env','.env.local','auth.json','.credentials.json','id_rsa','.ssh/config',
                         '.aws/credentials','.codex/sessions/chat.jsonl','credentials.json','private.pem',
                         'transcripts/chat.jsonl','transcript.jsonl','rollout-fixture.jsonl','AUTH.JSON',
                         '.ENV.local','id_ecdsa','secrets.json'):
            path=self.cwd/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('Private fixture')
            with self.subTest(path=relative):self.assert_rejected(path,'APPROVAL_BLOCKED')
        for path in (self.home/'context.txt',self.j.root/'bindings'/'context.txt'):
            path.write_text('Private state');self.assert_rejected(path,'APPROVAL_BLOCKED')
        custom=self.root/'custom-provider-home';custom.mkdir();private=custom/'context.txt';private.write_text('Private state')
        with patch.dict(os.environ,{'CODEX_HOME':str(custom)}):self.assert_rejected(private,'APPROVAL_BLOCKED')

    def test_import_design_brief_with_placeholder_succeeds(self):
        raw=b'# Design brief\nFixture examples: password=placeholder\nAPI_KEY=example\n{"access_token":"placeholder"}\n'
        for name in ('design-brief.md','transcript-design-brief.md','rollout-plan.md'):
            with self.subTest(name=name):
                path=self.cwd/name;path.write_bytes(raw)
                args=parser().parse_args(['artifact-add','--file',str(path),'--json'])
                with patch('nativepeers.cli.bind_caller',return_value=self.caller):
                    result=asyncio.run(dispatch(args,self.j))
                self.assertEqual(result['sha256'],hashlib.sha256(raw).hexdigest())
                self.assertEqual(result['byte_size'],len(raw))
                page=self.artifacts.read(result['artifact_id'],self.caller.key)
                self.assertEqual(base64.b64decode(page['data_base64']),raw)

    def test_invalid_byte_limits_and_oversize_files_leave_no_artifact(self):
        for limit in (0,-1,ARTIFACT_MAX+1,True,'10',1.5):
            with self.subTest(limit=limit):self.assert_rejected(self.path,'PROTOCOL_ERROR',max_bytes=limit)
        self.assert_rejected(self.path,'PAYLOAD_TOO_LARGE',max_bytes=1)
        with self.path.open('wb') as stream:stream.truncate(ARTIFACT_MAX+1)
        self.assert_rejected(self.path,'PAYLOAD_TOO_LARGE')

    def test_read_is_bounded_even_when_the_selected_file_grows(self):
        fdopen=os.fdopen;reads=[];limit=self.path.stat().st_size
        def tracked_open(fd,mode):
            stream=fdopen(fd,mode)
            if mode=='rb':
                read=stream.read
                def growing_read(size):
                    reads.append(size)
                    with self.path.open('ab') as output:output.write(b'x'*100)
                    return read(size)
                stream.read=growing_read
            return stream
        with patch('nativepeers.artifacts.os.fdopen',side_effect=tracked_open):
            self.assert_rejected(self.path,'PAYLOAD_TOO_LARGE',max_bytes=limit)
        self.assertEqual(reads,[limit+1])

    def test_same_size_source_change_during_read_is_rejected(self):
        fdopen=os.fdopen
        def changing_open(fd,mode):
            stream=fdopen(fd,mode)
            if mode=='rb':
                read=stream.read
                def changing_read(size):
                    raw=read(size);info=self.path.stat()
                    self.path.write_bytes(b'x'*len(raw))
                    os.utime(self.path,ns=(info.st_atime_ns,info.st_mtime_ns+1000000))
                    return raw
                stream.read=changing_read
            return stream
        with patch('nativepeers.artifacts.os.fdopen',side_effect=changing_open):
            self.assert_rejected(self.path,'PROTOCOL_ERROR')

    def test_leaf_symlink_substitution_between_stat_and_open_is_rejected(self):
        open_file=os.open;outside=self.root/'outside.txt';outside.write_text('Outside')
        def replacing_open(path,flags,*args,**kwargs):
            if path==self.path.name and 'dir_fd' in kwargs:
                self.path.unlink();self.path.symlink_to(outside)
            return open_file(path,flags,*args,**kwargs)
        with patch('nativepeers.artifacts.os.open',side_effect=replacing_open):
            self.assert_rejected(self.path,'PROTOCOL_ERROR')

    def test_regular_file_substitution_between_stat_and_open_is_rejected(self):
        open_file=os.open;replacement=self.cwd/'replacement.txt';replacement.write_text('Another file')
        def replacing_open(path,flags,*args,**kwargs):
            if path==self.path.name and 'dir_fd' in kwargs:replacement.replace(self.path)
            return open_file(path,flags,*args,**kwargs)
        with patch('nativepeers.artifacts.os.open',side_effect=replacing_open):
            self.assert_rejected(self.path,'PROTOCOL_ERROR')

    def test_cli_invalid_limit_reports_a_structured_error(self):
        output=io.StringIO()
        with patch('sys.argv',['npeer','artifact-add','--file',str(self.path),'--max-bytes','0','--json']),\
             patch('nativepeers.cli.Journal',return_value=self.j),\
             patch('nativepeers.cli.bind_caller',return_value=self.caller),patch('sys.stdout',output):
            with self.assertRaises(SystemExit) as error:main()
        self.assertNotEqual(error.exception.code,0)
        response=json.loads(output.getvalue());self.assertFalse(response['ok'])
        self.assertEqual(response['error']['code'],'PROTOCOL_ERROR')

    def test_import_has_no_mcp_or_core_file_read_operation(self):
        from nativepeers.mcp_server import TOOLS
        self.assertNotIn('artifact_add',TOOLS)
        with self.assertRaises(PeerError):asyncio.run(Core(self.j).tool(self.caller,'artifact_add',{'file':str(self.path)}))
