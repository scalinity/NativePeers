from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from nativepeers.errors import PeerError
from nativepeers.isolated_copy import MAX_FILES,capture
from nativepeers.journal import Journal
from nativepeers.protocol import validate
from nativepeers.worktrees import changes,freeze,git,prepare,snapshot,validate_frozen


class IsolatedCopyTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve();self.source=self.root/'source';self.source.mkdir()
        (self.source/'edit.txt').write_bytes(b'original\r\n')
        (self.source/'nested').mkdir();(self.source/'nested/tool').write_bytes(b'#!/bin/sh\nexit 0\n')
        (self.source/'nested/tool').chmod(0o751)
        (self.source/'unselected').write_text('not selected\n')
        (self.source/'.env').write_text('PRIVATE_FIXTURE\n')
        self.journal=Journal(self.root/'state');self.addCleanup(self.journal.close)

    def workspace(self,*paths):
        return {'mode':'isolated-copy','file_allowlist':list(paths)}

    def prepare(self,workspace,worker='worker',**kwargs):
        directory,identifier,baseline=prepare(self.journal,self.source,worker,workspace,**kwargs)
        self.assertEqual(identifier,worker)
        return Path(directory),baseline

    def source_files(self):
        return {str(p.relative_to(self.source)):(p.read_bytes(),stat.S_IMODE(p.stat().st_mode))
                for p in self.source.rglob('*') if p.is_file() and '.worktrees' not in p.parts}

    def test_exact_selected_bytes_modes_metadata_and_source_preservation(self):
        workspace=self.workspace('edit.txt','nested/tool');before=self.source_files();source=snapshot(self.source)
        frozen=freeze(self.source,[workspace],expected_baseline=source)
        work,baseline=self.prepare(workspace,frozen=frozen,expected_baseline=source)
        self.assertEqual(work,self.source/'.worktrees/worker');self.assertFalse(baseline['git'])
        self.assertEqual(baseline['cwd'],str(self.source.resolve()))
        self.assertEqual(baseline['workspace_mode'],'isolated-copy')
        self.assertFalse((self.source/'.git').exists());self.assertTrue((work/'.git').is_dir())
        self.assertFalse((work/'unselected').exists());self.assertFalse((work/'.env').exists())
        self.assertEqual(set(baseline['copy_inputs']),{'edit.txt','nested/tool'})
        for name in baseline['copy_inputs']:
            raw,mode=before[name]
            self.assertEqual((work/name).read_bytes(),raw);self.assertEqual(stat.S_IMODE((work/name).stat().st_mode),mode)
            self.assertEqual(baseline['copy_inputs'][name],{'sha256':hashlib.sha256(raw).hexdigest(),'mode':mode})
            self.assertNotEqual((work/name).stat().st_ino,(self.source/name).stat().st_ino)
        self.assertEqual(self.source_files(),before);validate_frozen(self.source,frozen)
        result=changes(work,baseline)
        self.assertEqual(result['patch'],b'');self.assertEqual(result['worker_delta'],b'')
        self.assertNotIn('worker_delta_unavailable_reason',result)

    def test_parallel_workers_materialize_identical_frozen_inputs_and_independent_changes(self):
        workspace=self.workspace('edit.txt','nested/tool');frozen=freeze(self.source,[workspace,workspace])
        with ThreadPoolExecutor(max_workers=2) as pool:
            first,second=list(pool.map(lambda name:self.prepare(workspace,worker=name,frozen=frozen),['first','second']))
        self.assertEqual(first[1]['input_tree'],second[1]['input_tree'])
        self.assertEqual(first[1]['copy_inputs'],second[1]['copy_inputs'])
        (first[0]/'edit.txt').write_text('first worker\n')
        self.assertEqual((second[0]/'edit.txt').read_bytes(),b'original\r\n')
        self.assertEqual((self.source/'edit.txt').read_bytes(),b'original\r\n')
        self.assertTrue(changes(first[0],first[1])['worker_delta'])
        self.assertEqual(changes(second[0],second[1])['worker_delta'],b'')

    def test_union_is_frozen_once_but_each_worker_gets_only_its_selection(self):
        first=self.workspace('edit.txt');second=self.workspace('nested/tool')
        with patch('nativepeers.isolated_copy.capture',wraps=capture) as captured:
            frozen=freeze(self.source,[first,second]);a,ba=self.prepare(first,worker='a',frozen=frozen);b,bb=self.prepare(second,worker='b',frozen=frozen)
        self.assertEqual(captured.call_count,1);self.assertEqual(set(frozen.untracked),{'edit.txt','nested/tool'})
        self.assertFalse((a/'nested').exists());self.assertFalse((b/'edit.txt').exists())
        self.assertEqual(set(ba['copy_inputs']),{'edit.txt'});self.assertEqual(set(bb['copy_inputs']),{'nested/tool'})
        with self.assertRaises(PeerError):self.prepare(self.workspace('unselected'),worker='unfrozen',frozen=frozen)

    def test_changed_selected_source_is_rejected_before_second_prepare_or_admission(self):
        for mutation in ('bytes','mode','deleted','symlink','directory','git'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as directory:
                source=Path(directory);path=source/'selected';path.write_text('frozen\n');path.chmod(0o640)
                workspace=self.workspace('selected');frozen=freeze(source,[workspace,workspace])
                prepare(self.journal,source,'first',workspace,frozen=frozen)
                if mutation=='bytes':path.write_text('changed\n')
                elif mutation=='mode':path.chmod(0o600)
                elif mutation=='deleted':path.unlink()
                elif mutation=='symlink':path.unlink();path.symlink_to(self.source/'edit.txt')
                elif mutation=='directory':path.unlink();path.mkdir()
                else:git(source,'init','-q');git(source,'add','selected');git(source,'commit','-qm','Create mutation fixture')
                with self.assertRaises(PeerError):validate_frozen(source,frozen)
                with self.assertRaises(PeerError):prepare(self.journal,source,'second',workspace,frozen=frozen)
                self.assertFalse((source/'.worktrees/second').exists())
                self.assertEqual((source/'.worktrees/first/selected').read_text(),'frozen\n')

    def test_patch_replays_edits_binary_new_files_deletions_and_executable_bit(self):
        workspace=self.workspace('edit.txt','nested/tool');before=self.source_files();work,baseline=self.prepare(workspace)
        (work/'edit.txt').write_bytes(b'worker\r\n');(work/'nested/tool').unlink()
        (work/'new.bin').write_bytes(b'\x00\x01worker\n');(work/'new.bin').chmod(0o751)
        result=changes(work,baseline)
        self.assertTrue(result['patch']);self.assertEqual(result['patch'],result['worker_delta'])
        replay=self.root/'replay';git(work,'clone','-q','--no-hardlinks',str(work),str(replay))
        git(replay,'apply','--binary','--index',input=result['patch'])
        self.assertEqual((replay/'edit.txt').read_bytes(),b'worker\r\n');self.assertFalse((replay/'nested/tool').exists())
        self.assertEqual((replay/'new.bin').read_bytes(),b'\x00\x01worker\n')
        self.assertTrue((replay/'new.bin').stat().st_mode&0o111);self.assertEqual(self.source_files(),before)

    def test_private_worker_outputs_and_symlinks_are_not_in_patches(self):
        work,baseline=self.prepare(self.workspace('edit.txt'))
        (work/'edit.txt').write_text('safe edit\n');(work/'.codex').mkdir();(work/'.codex/config.toml').write_text('PRIVATE_NATIVE_FIXTURE\n')
        (work/'.env').write_text('PRIVATE_SECRET_FIXTURE\n');(work/'link').symlink_to(work/'.env')
        result=changes(work,baseline)
        self.assertEqual(set(result['excluded_sensitive_or_symlink_files']),{'.codex/config.toml','.env','link'})
        for name in ('patch','worker_delta'):
            self.assertIn(b'+safe edit',result[name]);self.assertNotIn(b'PRIVATE_',result[name])

    def test_copy_initialization_and_delta_never_execute_filters_or_normalize_selected_bytes(self):
        marker=self.root/'filter-executed'
        hooks=self.root/'hooks';hooks.mkdir();hook=hooks/'reference-transaction'
        hook.write_text('#!/bin/sh\ntouch '+str(marker)+'\n');hook.chmod(0o700)
        (self.source/'.gitattributes').write_text('* text eol=lf ident filter=copy-fixture\n')
        (self.source/'edit.txt').write_bytes(b'$Id: expanded $\r\noriginal\r\n')
        config=self.root/'gitconfig';original=os.environ.get('GIT_CONFIG_GLOBAL')
        includes=[Path(original)] if original else [Path(os.environ.get('XDG_CONFIG_HOME',str(Path.home()/'.config')))/'git/config',Path.home()/'.gitconfig']
        config.write_text(''.join('[include]\n\tpath = '+str(path)+'\n' for path in includes if path.exists())
            +'[filter "copy-fixture"]\n\tclean = touch '+str(marker)+'\n\tsmudge = touch '+str(marker)+'\n\trequired = true\n'
            +'[core]\n\thooksPath = '+str(hooks)+'\n')
        with patch.dict(os.environ,{'GIT_CONFIG_GLOBAL':str(config)}):
            work,baseline=self.prepare(self.workspace('edit.txt','.gitattributes'))
            self.assertEqual(git(work,'show','HEAD:edit.txt').stdout,(self.source/'edit.txt').read_bytes())
            self.assertEqual(changes(work,baseline)['worker_delta'],b'')
            (work/'edit.txt').write_bytes(b'$Id: expanded $\r\nworker\r\n');result=changes(work,baseline)
        self.assertFalse(marker.exists());self.assertIn(b'+worker\r\n',result['worker_delta'])

    def test_unsafe_missing_recursive_and_special_file_selections_are_rejected(self):
        (self.source/'link').symlink_to(self.source/'edit.txt')
        (self.source/'linked-directory').symlink_to(self.source/'nested',target_is_directory=True)
        os.mkfifo(self.source/'fifo')
        paths=['','.', './edit.txt','../edit.txt',str(self.source/'edit.txt'),'nested','missing','link',
               'linked-directory/tool','fifo','.env','.ENV','nested/.env.local','.git/config','.worktrees/x',
               '.nativepeers/journal.sqlite','.codex/config.toml','.claude/settings.json','.gemini/settings.json',
               'auth.json','token.json','key.pem','rollout-fixture.jsonl','transcripts/a.jsonl','edit.txt\x00']
        for name in paths:
            with self.subTest(path=name),self.assertRaises(PeerError):freeze(self.source,[self.workspace(name)])
        for paths in ([],['edit.txt','edit.txt'],['edit.txt']*(MAX_FILES+1)):
            with self.subTest(paths=len(paths)),self.assertRaises(PeerError):freeze(self.source,[self.workspace(*paths)])
        for workspace in ({'mode':'isolated-copy'},{'mode':'isolated-copy','file_allowlist':'edit.txt'},
                          {'mode':'isolated-copy','file_allowlist':['edit.txt'],'untracked_allowlist':['unselected']}):
            with self.subTest(workspace=workspace),self.assertRaises(PeerError):freeze(self.source,[workspace])
        self.assertFalse((self.source/'.worktrees').exists());self.assertFalse((self.source/'.git').exists())

    def test_batch_byte_and_file_bounds_apply_to_union(self):
        with patch('nativepeers.isolated_copy.ARTIFACT_MAX',20):
            with self.assertRaises(PeerError):freeze(self.source,[self.workspace('edit.txt'),self.workspace('nested/tool')])
        with patch('nativepeers.isolated_copy.ARTIFACT_MAX',10):
            frozen=freeze(self.source,[self.workspace('edit.txt')]);self.assertEqual(frozen.untracked['edit.txt'][0],b'original\r\n')
        with patch('nativepeers.isolated_copy.MAX_FILES',1):
            with self.assertRaises(PeerError):freeze(self.source,[self.workspace('edit.txt'),self.workspace('nested/tool')])

    def test_existing_copy_or_symlink_storage_is_never_overwritten(self):
        workspace=self.workspace('edit.txt');work,_=self.prepare(workspace)
        (work/'edit.txt').write_text('retained worker edit\n')
        with self.assertRaises(FileExistsError):self.prepare(workspace)
        self.assertEqual((work/'edit.txt').read_text(),'retained worker edit\n')
        other=self.root/'other';other.mkdir();(other/'selected').write_text('input\n')
        (other/'.worktrees').symlink_to(self.root/'elsewhere')
        with self.assertRaises((PeerError,PermissionError,FileExistsError)):
            prepare(self.journal,other,'outside',self.workspace('selected'))
        self.assertFalse((self.root/'elsewhere').exists())

    def test_source_change_during_materialization_removes_only_incomplete_owned_copy(self):
        workspace=self.workspace('edit.txt');frozen=freeze(self.source,[workspace]);calls=[]
        def mutate(cwd,*args,**kwargs):
            calls.append((Path(cwd),args))
            result=git(cwd,*args,**kwargs)
            if 'update-ref' in args:(self.source/'edit.txt').write_text('changed during preparation\n')
            return result
        with patch('nativepeers.isolated_copy.git',side_effect=mutate):
            with self.assertRaises(PeerError):self.prepare(workspace,frozen=frozen)
        self.assertTrue(calls);self.assertTrue(all(cwd==self.source/'.worktrees/worker' for cwd,args in calls))
        self.assertFalse((self.source/'.worktrees/worker').exists());self.assertFalse((self.source/'.git').exists())
        self.assertEqual((self.source/'edit.txt').read_text(),'changed during preparation\n')

    def test_private_native_sources_are_rejected_and_readonly_inputs_cannot_be_reused(self):
        home=self.source/'custom-home';home.mkdir();(home/'config.toml').write_text('private native fixture\n')
        for name in ('CODEX_HOME','CLAUDE_CONFIG_DIR','NPEER_HOME'):
            with self.subTest(home=name),patch.dict(os.environ,{name:str(self.source)}):
                with self.assertRaises(PeerError):freeze(self.source,[self.workspace('edit.txt')])
            with self.subTest(nested_home=name),patch.dict(os.environ,{name:str(home)}):
                with self.assertRaises(PeerError):freeze(self.source,[self.workspace('custom-home/config.toml')])
        frozen=freeze(self.source,[{'mode':'inherit-read-only'}])
        with self.assertRaises(PeerError):self.prepare(self.workspace('edit.txt'),frozen=frozen)
        for identifier in ('../outside','/absolute','.','..',''):
            with self.subTest(worker=identifier),self.assertRaises(PeerError):
                self.prepare(self.workspace('edit.txt'),worker=identifier)
        self.assertFalse((self.source/'.git').exists());self.assertFalse((self.source/'.worktrees').exists())

    def test_git_sources_mixed_modes_and_mismatched_baselines_are_rejected(self):
        workspace=self.workspace('edit.txt')
        with self.assertRaises(PeerError):freeze(self.source,[workspace,{'mode':'isolated-worktree'}])
        with self.assertRaises(PeerError):freeze(self.source,[workspace],expected_baseline={'git':False,'cwd':'/wrong'})
        frozen=freeze(self.source,[workspace])
        with self.assertRaises(PeerError):self.prepare(workspace,frozen=frozen,expected_baseline={'git':False,'cwd':'/wrong'})
        other=self.root/'different-source';other.mkdir();(other/'edit.txt').write_bytes(b'original\r\n')
        with self.assertRaises(PeerError):prepare(self.journal,other,'worker',workspace,frozen=frozen)
        git(self.source,'init','-q');git(self.source,'add','edit.txt');git(self.source,'commit','-qm','Create Git source fixture')
        with self.assertRaises(PeerError):freeze(self.source,[workspace])

    def test_spawn_schema_accepts_copy_fields_and_rejects_bad_types_and_bounds(self):
        schema=json.loads((Path(__file__).resolve().parents[2]/'schemas/spawn.v1.json').read_text())
        worker={'provider':'claude','title':'copy-fixture','task':'fixture','permission_profile':'workspace'}
        validate({'workers':[dict(worker,workspace=self.workspace('edit.txt'))]},schema)
        for workspace in ({'mode':'unknown'},{'mode':'isolated-copy','file_allowlist':[]},
                          {'mode':'isolated-copy','file_allowlist':[1]},self.workspace(*(['edit.txt']*257))):
            with self.subTest(workspace=workspace),self.assertRaises(PeerError):validate({'workers':[dict(worker,workspace=workspace)]},schema)

    def test_registered_nonambient_homes_and_canonical_journal_are_private(self):
        from nativepeers.registry import Registry
        from nativepeers.process import identity
        from nativepeers.models import new_id
        home=self.source/'alternate';home.mkdir();(home/'settings').write_text('private fixture')
        peer=Registry(self.journal).register('codex',home,new_id(),'fixture',identity(os.getpid()).json(),str(self.source),{},owner_kind='test')
        self.journal.db.execute("UPDATE peer_instances SET status='offline' WHERE incarnation_id=?",(peer['incarnation_id'],))
        for source,selected in [(self.source,'alternate/settings'),(home,'settings'),(self.root,'state/host.json'),(self.journal.root,'host.json')]:
            with self.subTest(source=source,selected=selected),self.assertRaises(PeerError):
                freeze(source,[self.workspace(selected)],journal=self.journal)
        self.assertFalse((self.source/'.worktrees').exists())

    def test_private_roots_are_rechecked_after_freeze_and_in_executor_threads(self):
        from nativepeers.registry import Registry
        from nativepeers.process import identity
        from nativepeers.models import new_id
        home=self.source/'alternate';home.mkdir();(home/'input').write_text('fixture')
        workspace=self.workspace('alternate/input')
        with ThreadPoolExecutor(max_workers=1) as pool:
            frozen=pool.submit(freeze,self.source,[workspace],journal=self.journal).result()
        Registry(self.journal).register('antigravity',home,new_id(),'fixture',identity(os.getpid()).json(),str(home),{},owner_kind='test')
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.assertRaises(PeerError):pool.submit(validate_frozen,self.source,frozen).result()
            with self.assertRaises(PeerError):pool.submit(prepare,self.journal,self.source,'private',workspace,frozen=frozen).result()
        self.assertFalse((self.source/'.worktrees/private').exists())
