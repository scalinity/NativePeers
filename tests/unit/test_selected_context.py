"""Exact prepared context and artifact handoffs without shell/profile escalation."""
import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nativepeers.artifacts import Artifacts
from nativepeers.caller import Caller
from nativepeers.core import Core
from nativepeers.errors import PeerError
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.registry import Registry
from nativepeers.selected_context import prepare
from nativepeers.selected_context import compare
from nativepeers.worktrees import git


class SelectedContextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve();self.repo=self.root/'source';self.repo.mkdir()
        self.j=Journal(self.root/'state');self.addCleanup(self.j.close)
        registry=Registry(self.j);self.registry=registry
        peer=registry.register('codex',self.root/'native-home',new_id(),'parent',identity(os.getpid()).json(),str(self.repo),{'kind':'fake'},owner_kind='test')
        self.caller=Caller('peer',peer['handle'],peer['incarnation_id'])
        git(self.repo,'init','-q');(self.repo/'a.py').write_text('value=1\n')
        git(self.repo,'add','a.py');git(self.repo,'commit','-qm','Create selected context fixture')

    def packet(self,**kw):return prepare(self.j,self.caller,['a.py'],**kw)

    def read(self,info):return json.loads(Artifacts(self.j).read(info['artifact_id'],self.caller.key,text=True)['text'])

    def test_committed_dirty_staged_and_untracked_are_frozen_without_index_changes(self):
        (self.repo/'a.py').write_text('value=2\n');git(self.repo,'add','a.py');(self.repo/'a.py').write_text('value=3\n')
        (self.repo/'new.py').write_text('new=4\n');index=(self.repo/'.git/index').read_bytes()
        info=prepare(self.j,self.caller,['a.py','new.py'],untracked=['new.py'])
        packet=self.read(info);a,new=packet['files']
        self.assertEqual(a['base']['text'],'value=1\n');self.assertEqual(a['current']['text'],'value=3\n')
        self.assertIn('+value=3',packet['diff']['text']);self.assertTrue(new['untracked']);self.assertIsNone(new['base'])
        self.assertIn('4d4d',packet['source']['selected_status_hex']) # MM staged and unstaged
        self.assertEqual((self.repo/'.git/index').read_bytes(),index)
        (self.repo/'a.py').write_text('value=5\n');self.assertEqual(self.read(info),packet)
        self.assertTrue(packet['boundaries']['full_selected_content']);self.assertIsNone(packet['boundaries']['worker_delta'])

    def test_explicit_selection_privacy_symlinks_limits_and_untracked(self):
        (self.repo/'new.py').write_text('safe\n')
        for paths,kw in [(['new.py'],{}),(['../a.py'],{}),(['.env'],{}),(['a.py'],{'max_bytes':8})]:
            with self.subTest(paths=paths),self.assertRaises((PeerError,OSError)):prepare(self.j,self.caller,paths,**kw)
        (self.repo/'link.py').symlink_to(self.repo/'a.py')
        with self.assertRaises(PeerError):prepare(self.j,self.caller,['link.py'],untracked=['link.py'])
        home=self.repo/'cache';home.mkdir();(home/'x.py').write_text('private fixture')
        self.registry.register('claude',home,new_id(),'offline',identity(os.getpid()).json(),str(home),{},owner_kind='test')
        self.j.db.execute("UPDATE peer_instances SET status='live' WHERE peer_key=?",(self.caller.key,))
        with self.assertRaises(PeerError):prepare(self.j,self.caller,['cache/x.py'],untracked=['cache/x.py'])

    def test_external_diff_textconv_filters_and_pagers_are_not_executed(self):
        marker=self.root/'external-ran'
        git(self.repo,'config','diff.external','touch '+str(marker))
        git(self.repo,'config','diff.fixture.textconv','touch '+str(marker))
        git(self.repo,'config','core.pager','touch '+str(marker))
        (self.repo/'.gitattributes').write_text('a.py diff=fixture\n')
        (self.repo/'a.py').write_text('value=2\n')
        self.assertIn('+value=2',self.read(self.packet())['diff']['text']);self.assertFalse(marker.exists())
        git(self.repo,'config','filter.fixture.clean','touch '+str(marker))
        (self.repo/'.gitattributes').write_text('a.py filter=fixture\n')
        with self.assertRaises(PeerError):self.packet()
        self.assertFalse(marker.exists())

    def test_handoff_comparison_distinguishes_frozen_source_and_current_overlap(self):
        info=self.packet()
        unchanged=compare(self.j,self.caller,info['artifact_id'])
        self.assertEqual(unchanged['changed_paths'],[]);self.assertTrue(unchanged['source']['head_matches'])
        (self.repo/'a.py').write_text('value=9\n');(self.repo/'unrelated.txt').write_text('untouched by comparison')
        index=(self.repo/'.git/index').read_bytes()
        changed=compare(self.j,self.caller,info['artifact_id'])
        self.assertEqual(changed['changed_paths'],['a.py']);self.assertFalse(changed['mutation'])
        self.assertEqual((self.repo/'.git/index').read_bytes(),index)
        self.assertEqual((self.repo/'unrelated.txt').read_text(),'untouched by comparison')
        self.assertEqual(self.read(info)['files'][0]['current']['text'],'value=1\n')

    async def test_authorized_message_artifact_read_grants_and_receipts_stay_distinct(self):
        info=self.packet();core=Core(self.j)
        # A second independently registered parent receives the selected packet.
        peer=self.registry.register('claude',self.root/'other-home',new_id(),'recipient',identity(os.getpid()).json(),str(self.repo),{'kind':'fake'},owner_kind='test')
        self.j.db.execute("UPDATE peer_instances SET status='live' WHERE peer_key=?",(self.caller.key,))
        core.router.adapters['fake']=lambda *_:None
        from unittest.mock import AsyncMock
        core.router.adapters['fake']=AsyncMock(return_value={'stage':'ACCEPTED_QUEUED','evidence_kind':'fixture-admission'})
        recipient=Caller('peer',peer['handle'],peer['incarnation_id'])
        with self.assertRaises(PeerError):Artifacts(self.j).read(info['artifact_id'],recipient.key)
        outcome=await core.tool(self.caller,'peer_send',dict(target=peer['handle'],text='Selected ownership snapshot; compare a.py before integration.',client_request_id='handoff',artifact_refs=info['context_refs']))
        self.assertEqual(outcome['state'],'ACCEPTED_QUEUED')
        data=await core.tool(recipient,'artifact_read',dict(handle=info['artifact_id'],text=True))
        self.assertEqual(json.loads(data['text'])['files'][0]['path'],'a.py')
        self.assertEqual([r['stage'] for r in self.j.all('SELECT * FROM receipts')],['ACCEPTED_QUEUED'])
        self.assertEqual(self.j.one('SELECT state FROM attempts')['state'],'ACCEPTED_QUEUED')

    def test_non_utf8_diff_is_exact_base64_and_source_is_still_available(self):
        (self.repo/'a.py').write_bytes(b'caf\xe9=2\n')
        packet=self.read(self.packet())
        self.assertIn('base64',packet['diff'])
        self.assertIn(b'caf\xe9',base64.b64decode(packet['diff']['base64']))
        self.assertEqual(base64.b64decode(packet['files'][0]['current']['base64']),b'caf\xe9=2\n')

    def test_text_pages_preserve_multibyte_characters_at_every_boundary(self):
        raw=('x'+('猫💡é'*20)).encode();handle=Artifacts(self.j).put(raw,self.caller.key)
        for limit in range(4,16):
            offset=0;parts=[]
            while offset is not None:
                page=Artifacts(self.j).read(handle,self.caller.key,offset,limit,text=True)
                self.assertFalse(page['text_decode_lossy']);self.assertLessEqual(page['bytes_read'],limit)
                self.assertGreater(page['bytes_read'],0);parts.append(page['text']);offset=page['next_offset']
            self.assertEqual(''.join(parts).encode(),raw)
        with self.assertRaises(PeerError):Artifacts(self.j).read(handle,self.caller.key,2,5,text=True)
        handle=Artifacts(self.j).put(b'caf\xe9',self.caller.key)
        with self.assertRaises(PeerError):Artifacts(self.j).read(handle,self.caller.key,text=True)
        self.assertEqual(base64.b64decode(Artifacts(self.j).read(handle,self.caller.key)['data_base64']),b'caf\xe9')

    def test_replaced_file_types_are_changed_paths_and_missing_root_has_supported_route(self):
        info=self.packet();path=self.repo/'a.py';path.unlink();path.mkdir()
        value=compare(self.j,self.caller,info['artifact_id']);self.assertEqual(value['changed_paths'],['a.py'])
        self.assertIn('unavailable',value['files'][0]);path.rmdir();path.symlink_to(self.repo/'absent')
        self.assertEqual(compare(self.j,self.caller,info['artifact_id'])['changed_paths'],['a.py'])
        old=self.repo;old.rename(self.root/'renamed')
        with self.assertRaises(PeerError) as error:compare(self.j,self.caller,info['artifact_id'])
        self.assertIn('--project',error.exception.message)
        self.assertEqual(compare(self.j,self.caller,info['artifact_id'],project=self.root/'renamed')['changed_paths'],['a.py'])

    def test_selected_facade_import_ignores_a_shadow_package_in_native_cwd(self):
        import subprocess
        from nativepeers.profiles import facade_config,ROOT
        shadow=self.root/'shadow';(shadow/'nativepeers').mkdir(parents=True)
        (shadow/'nativepeers/__init__.py').write_text('raise RuntimeError("wrong source")\n')
        config=facade_config(self.j,self.j.root/'bindings/unused')
        # Reuse all selected interpreter flags, substituting only the entry module with an import observation.
        flags=config['args'][:config['args'].index('-m')]
        process=subprocess.run([config['command'],*flags,'-c','import nativepeers; print(nativepeers.__file__)'],cwd=shadow,
                               env=dict(os.environ,**config['env']),capture_output=True,text=True,timeout=10)
        self.assertEqual(process.returncode,0,process.stderr)
        self.assertEqual(Path(process.stdout.strip()).resolve(),ROOT/'nativepeers/__init__.py')

    def test_mode_only_changes_and_missing_preparation_root(self):
        info=self.packet();path=self.repo/'a.py';old=path.stat().st_mode & 0o777
        path.chmod(old ^ 0o111)
        changed=compare(self.j,self.caller,info['artifact_id']);self.assertEqual(changed['changed_paths'],['a.py'])
        self.assertEqual(changed['files'][0]['current_sha256'],changed['files'][0]['frozen_sha256'])
        self.assertEqual(self.read(self.packet())['files'][0]['current_mode'],old ^ 0o111)
        from nativepeers.artifacts import _selected_file_bytes
        self.assertEqual(_selected_file_bytes(path,1024),b'value=1\n')
        with self.assertRaises(PeerError) as error:self.packet(project=self.root/'missing')
        self.assertIn('--project',error.exception.message)

    def test_private_absolute_repository_components_match_direct_artifact_import_rules(self):
        info=self.packet();private=self.root/'.aws';private.mkdir();moved=private/'source';self.repo.rename(moved)
        for operation in (lambda:prepare(self.j,self.caller,['a.py'],project=moved),
                          lambda:compare(self.j,self.caller,info['artifact_id'],project=moved),
                          lambda:Artifacts(self.j).add_file(moved/'a.py',self.caller)):
            with self.assertRaises(PeerError) as error:operation()
            self.assertEqual(error.exception.code,'APPROVAL_BLOCKED')

    def test_comparison_normalizes_the_same_subdirectory_argument_as_preparation(self):
        folder=self.repo/'src';folder.mkdir();(folder/'a.py').write_text('nested=1\n')
        git(self.repo,'add','src/a.py');git(self.repo,'commit','-qm','Create nested selected input')
        info=prepare(self.j,self.caller,['src/a.py'],project=folder)
        value=compare(self.j,self.caller,info['artifact_id'],project=folder)
        self.assertEqual(value['changed_paths'],[]);self.assertTrue(value['source']['head_matches'])

    def test_runtime_revocation_changes_approval_even_when_executable_bytes_match(self):
        from nativepeers.compatibility import diagnose,fingerprint,revoke
        from nativepeers.files import atomic_json
        binary=self.root/'codex-fixture';binary.write_text('non-executed runtime fixture')
        runtime=fingerprint(binary);digest=runtime['sha256']
        atomic_json(self.j.root/'compat/runtime.json',dict(providers=dict(codex=runtime),retained=dict(codex={digest:dict(runtime,revoked=False)})))
        original=diagnose
        with patch('nativepeers.compatibility.diagnose',side_effect=lambda journal:original(journal,executables={'codex':binary})):
            info=self.packet(runtimes=['codex'])
            self.assertTrue(compare(self.j,self.caller,info['artifact_id'])['runtimes'][0]['matches'])
            revoke(self.j,'codex',digest)
            changed=compare(self.j,self.caller,info['artifact_id'])['runtimes'][0]
        self.assertFalse(changed['matches']);self.assertTrue(changed['selected_matches']);self.assertFalse(changed['approval_matches'])
        self.assertEqual(changed['current']['compatibility_state'],'REVOKED')
