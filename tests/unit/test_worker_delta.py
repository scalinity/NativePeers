from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from nativepeers.journal import Journal
from nativepeers.worktrees import changes,git,prepare,snapshot


class WorkerDeltaTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.repo=self.root/'repo';self.repo.mkdir()
        git(self.repo,'init','-q')
        for name in ['parent.txt','edit.txt','removed.txt']:(self.repo/name).write_text('baseline\n')
        git(self.repo,'add','.');git(self.repo,'commit','-qm','Create worker delta fixture')
        self.journal=Journal(self.root/'state');self.addCleanup(self.journal.close)
        override=patch('nativepeers.worktrees.ROOT',self.root/'project');override.start();self.addCleanup(override.stop)

    def prepare(self,*selected,worker='worker'):
        directory,_,baseline=prepare(self.journal,self.repo,worker,
            {'mode':'isolated-worktree','untracked_allowlist':list(selected)})
        return Path(directory),baseline

    def index_bytes(self,cwd):
        path=Path(git(cwd,'rev-parse','--git-path','index').stdout.decode().strip())
        return (path if path.is_absolute() else cwd/path).read_bytes()

    def replay(self,name,tree,patch_bytes):
        directory=self.repo/'.worktrees'/name
        git(self.repo,'worktree','add','--detach',str(directory),'HEAD')
        git(directory,'read-tree','--reset','-u',tree)
        if patch_bytes:git(directory,'apply','--binary','--index',input=patch_bytes)
        return directory

    def test_noop_has_empty_delta_but_preserves_full_reconstruction(self):
        parent=self.repo/'parent.txt';parent.write_text('parent staged\n');git(self.repo,'add','parent.txt');parent.write_text('parent unstaged\n')
        (self.repo/'removed.txt').unlink();(self.repo/'selected').write_bytes(b'\x00parent input\n');(self.repo/'selected').chmod(0o751)
        source_before=snapshot(self.repo);source_index=self.index_bytes(self.repo)
        work,baseline=self.prepare('selected');worker_index=self.index_bytes(work)
        result=changes(work,baseline)
        self.assertTrue(result['patch']);self.assertEqual(result['worker_delta'],b'')
        self.assertNotIn('worker_delta_unavailable_reason',result)
        self.assertEqual(result['worker_delta_base'],baseline['input_tree'])
        self.assertEqual(git(work,'cat-file','-t',baseline['input_tree']).stdout,b'tree\n')
        self.assertEqual(git(work,'show',baseline['input_tree']+':selected').stdout,b'\x00parent input\n')
        self.assertTrue(git(work,'ls-tree',baseline['input_tree'],'--','selected').stdout.startswith(b'100755 '))
        self.assertEqual(git(work,'rev-parse','HEAD').stdout.decode().strip(),baseline['head'])
        self.assertEqual(snapshot(self.repo),source_before);self.assertEqual(self.index_bytes(self.repo),source_index)
        self.assertEqual(self.index_bytes(work),worker_index)
        self.assertFalse(list((self.repo/'.git').rglob('npeer-index-*')))

    def test_delta_excludes_parent_edits_and_replays_worker_files(self):
        (self.repo/'parent.txt').write_text('PARENT_ONLY\n');(self.repo/'selected').write_text('parent selected\n')
        source_before=snapshot(self.repo);work,baseline=self.prepare('selected')
        (work/'edit.txt').write_text('worker edit\n');(work/'selected').write_text('worker selected\n')
        (work/'new.bin').write_bytes(b'\x00\x01worker binary\n');(work/'new.bin').chmod(0o751)
        (work/'staged.txt').write_text('worker staged\n');git(work,'add','staged.txt')
        worker_index=self.index_bytes(work);result=changes(work,baseline)
        self.assertIn(b'+PARENT_ONLY',result['patch']);self.assertNotIn(b'parent.txt',result['worker_delta'])
        for marker in [b'worker edit',b'worker selected',b'new.bin',b'worker staged']:
            self.assertIn(marker,result['worker_delta'])
        full=self.replay('full',baseline['head'],result['patch'])
        delta=self.replay('delta',baseline['input_tree'],result['worker_delta'])
        for name in ['parent.txt','edit.txt','selected','new.bin','staged.txt']:
            self.assertEqual((full/name).read_bytes(),(work/name).read_bytes())
            self.assertEqual((delta/name).read_bytes(),(work/name).read_bytes())
        self.assertEqual(stat.S_IMODE((delta/'new.bin').stat().st_mode)&0o111,0o111)
        self.assertEqual(self.index_bytes(work),worker_index);self.assertEqual(snapshot(self.repo),source_before)

    def test_inherited_staged_rename_has_empty_delta_and_complete_reconstruction(self):
        git(self.repo,'mv','edit.txt','renamed.txt')
        source_before=snapshot(self.repo);work,baseline=self.prepare()
        result=changes(work,baseline)
        self.assertEqual(result['worker_delta'],b'')
        self.assertNotIn('worker_delta_unavailable_reason',result)
        for view,tree in [('patch',baseline['head']),('worker_delta',baseline['input_tree'])]:
            replay=self.replay('rename-'+view,tree,result[view])
            self.assertFalse((replay/'edit.txt').exists())
            self.assertEqual((replay/'renamed.txt').read_bytes(),(work/'renamed.txt').read_bytes())
        self.assertFalse((work/'edit.txt').exists());self.assertEqual(snapshot(self.repo),source_before)

    def test_worker_staged_and_committed_renames_replay_both_patch_views(self):
        for committed in (False,True):
            with self.subTest(committed=committed):
                suffix='committed' if committed else 'staged'
                work,baseline=self.prepare(worker='rename-'+suffix)
                git(work,'mv','edit.txt','renamed.txt')
                if committed:git(work,'commit','-qm','Rename worker fixture')
                worker_index=self.index_bytes(work);result=changes(work,baseline)
                self.assertEqual(set(result['changed_files']),{'edit.txt','renamed.txt'})
                for view,tree in [('patch',baseline['head']),('worker_delta',baseline['input_tree'])]:
                    replay=self.replay(suffix+'-'+view,tree,result[view])
                    self.assertFalse((replay/'edit.txt').exists())
                    self.assertEqual((replay/'renamed.txt').read_bytes(),(work/'renamed.txt').read_bytes())
                self.assertEqual(self.index_bytes(work),worker_index)

    def test_crlf_dirty_input_has_empty_noop_delta_and_replays_real_edit(self):
        (self.repo/'.gitattributes').write_text('edit.txt text eol=crlf\n')
        git(self.repo,'add','.gitattributes');git(self.repo,'commit','-qm','Set fixture line endings')
        (self.repo/'edit.txt').write_bytes(b'parent dirty\r\n')
        source_before=snapshot(self.repo);work,baseline=self.prepare()
        self.assertEqual((work/'edit.txt').read_bytes(),b'parent dirty\r\n')
        worker_index=self.index_bytes(work);noop=changes(work,baseline)
        self.assertEqual(noop['worker_delta'],b'');self.assertNotIn('worker_delta_unavailable_reason',noop)
        (work/'edit.txt').write_bytes(b'worker edit\r\n');result=changes(work,baseline)
        self.assertIn(b'-parent dirty\n+worker edit\n',result['worker_delta'])
        for view,tree in [('patch',baseline['head']),('worker_delta',baseline['input_tree'])]:
            replay=self.replay('crlf-'+view,tree,result[view])
            self.assertEqual((replay/'edit.txt').read_bytes(),b'worker edit\r\n')
            self.assertEqual(git(replay,'show',':edit.txt').stdout,b'worker edit\n')
        self.assertEqual(self.index_bytes(work),worker_index);self.assertEqual(snapshot(self.repo),source_before)

    def test_builtin_ident_conversion_does_not_appear_as_worker_edit(self):
        (self.repo/'.gitattributes').write_text('edit.txt ident\n')
        (self.repo/'edit.txt').write_text('$Id$\n')
        git(self.repo,'add','.');git(self.repo,'commit','-qm','Set fixture ident attribute')
        (self.repo/'edit.txt').write_text('$Id: fixture-expanded-id $\nparent dirty\n')
        work,baseline=self.prepare();result=changes(work,baseline)
        self.assertIn(b'$Id:',(work/'edit.txt').read_bytes())
        self.assertEqual(result['worker_delta'],b'');self.assertNotIn('worker_delta_unavailable_reason',result)

    def test_selected_untracked_input_keeps_its_raw_representation(self):
        (self.repo/'.gitattributes').write_text('selected text eol=crlf\n')
        git(self.repo,'add','.gitattributes');git(self.repo,'commit','-qm','Set selected input attributes')
        (self.repo/'selected').write_bytes(b'raw selected\r\n')
        work,baseline=self.prepare('selected');result=changes(work,baseline)
        self.assertEqual(result['worker_delta'],b'');self.assertNotIn('worker_delta_unavailable_reason',result)

    def test_custom_filters_are_not_executed_and_delta_is_explicitly_unavailable(self):
        work,baseline=self.prepare();marker=self.root/'filter-executed'
        (work/'.gitattributes').write_text('edit.txt filter=fixture\n')
        (work/'edit.txt').write_text('worker edit\n')
        for kind in ('clean','smudge','process'):
            git(work,'config','filter.fixture.'+kind,'touch '+str(marker))
        git(work,'config','filter.fixture.required','true')
        worker_index=self.index_bytes(work);result=changes(work,baseline)
        self.assertFalse(marker.exists())
        self.assertIn(b'+worker edit',result['patch'])
        self.assertEqual(result['worker_delta'],b'')
        self.assertIn('Custom Git filter',result['worker_delta_unavailable_reason'])
        self.assertEqual(self.index_bytes(work),worker_index)
        self.assertFalse(list((self.repo/'.git').rglob('npeer-index-*')))

    def test_reverting_parent_edits_and_deleting_input_still_produces_delta(self):
        (self.repo/'edit.txt').write_text('parent dirty\n');(self.repo/'selected').write_text('selected input\n')
        work,baseline=self.prepare('selected');(work/'edit.txt').write_text('baseline\n');(work/'selected').unlink()
        result=changes(work,baseline)
        self.assertEqual(result['patch'],b'');self.assertTrue(result['worker_delta'])
        self.assertIn(b'-parent dirty',result['worker_delta']);self.assertIn(b'-selected input',result['worker_delta'])
        replay=self.replay('reverted',baseline['input_tree'],result['worker_delta'])
        self.assertEqual((replay/'edit.txt').read_text(),'baseline\n');self.assertFalse((replay/'selected').exists())
        self.assertEqual((self.repo/'edit.txt').read_text(),'parent dirty\n')

    def test_patch_and_delta_have_independent_byte_bounds(self):
        (self.repo/'parent.txt').write_text('P'*100000+'\n');work,baseline=self.prepare()
        (work/'edit.txt').write_text('small worker edit\n')
        result=changes(work,baseline,max_bytes=1024)
        self.assertEqual(result['patch'],b'');self.assertIn('patch_unavailable_reason',result)
        self.assertIn(b'+small worker edit',result['worker_delta']);self.assertLessEqual(len(result['worker_delta']),1024)
        self.assertNotIn('worker_delta_unavailable_reason',result)
        (work/'new').write_text('W'*100000+'\n')
        result=changes(work,baseline,max_bytes=1024)
        self.assertEqual(result['worker_delta'],b'');self.assertIn('worker_delta_unavailable_reason',result)
        self.assertFalse(list((self.repo/'.git').rglob('npeer-index-*')))

    def test_secrets_and_symlinks_are_excluded_from_both_patches(self):
        work,baseline=self.prepare();(work/'edit.txt').write_text('safe worker edit\n')
        (work/'.env').write_text('PRIVATE_FIXTURE_DO_NOT_COPY\n');(work/'link').symlink_to(work/'.env')
        result=changes(work,baseline)
        self.assertEqual(set(result['excluded_sensitive_or_symlink_files']),{'.env','link'})
        for name in ['patch','worker_delta']:
            self.assertIn(b'+safe worker edit',result[name]);self.assertNotIn(b'PRIVATE_FIXTURE',result[name])
            self.assertNotIn(b'diff --git a/link',result[name])

    def test_missing_input_tree_and_expired_capture_are_explicitly_unavailable(self):
        work,baseline=self.prepare();(work/'new').write_text('worker\n')
        old={k:v for k,v in baseline.items() if k!='input_tree'}
        legacy=changes(work,old)
        self.assertTrue(legacy['patch']);self.assertEqual(legacy['worker_delta'],b'')
        self.assertIsNone(legacy['worker_delta_base']);self.assertIn('worker_delta_unavailable_reason',legacy)
        worker_index=self.index_bytes(work);timed=changes(work,baseline,timeout=0)
        self.assertEqual(timed['worker_delta'],b'');self.assertIn('worker_delta_unavailable_reason',timed)
        self.assertEqual(self.index_bytes(work),worker_index)

    def test_changed_ignore_rules_do_not_drop_materialized_inputs(self):
        (self.repo/'selected').write_text('frozen selected input\n');work,baseline=self.prepare('selected')
        (work/'.gitignore').write_text('selected\n')
        result=changes(work,baseline)
        self.assertIn(b'frozen selected input',result['patch'])
        self.assertNotIn(b'diff --git a/selected',result['worker_delta'])
        self.assertIn(b'+selected',result['worker_delta'])
        full=self.replay('ignored-full',baseline['head'],result['patch'])
        delta=self.replay('ignored-delta',baseline['input_tree'],result['worker_delta'])
        self.assertEqual((full/'selected').read_bytes(),(work/'selected').read_bytes())
        self.assertEqual((delta/'selected').read_bytes(),(work/'selected').read_bytes())

    def test_failed_private_index_capture_retains_full_patch_and_cleans_up(self):
        work,baseline=self.prepare();(work/'new').write_text('worker\n');worker_index=self.index_bytes(work)
        def fail_tree(cwd,*args,**kwargs):
            if args[0]=='write-tree' and kwargs.get('env',{}).get('GIT_INDEX_FILE'):
                raise OSError('fixture tree write failure')
            return git(cwd,*args,**kwargs)
        with patch('nativepeers.worktrees.git',side_effect=fail_tree):result=changes(work,baseline)
        self.assertIn(b'+worker',result['patch']);self.assertEqual(result['worker_delta'],b'')
        self.assertIn('worker_delta_unavailable_reason',result);self.assertEqual(self.index_bytes(work),worker_index)
        self.assertFalse(list((self.repo/'.git').rglob('npeer-index-*')))
