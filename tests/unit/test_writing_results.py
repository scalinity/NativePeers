from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.worktrees import changes,git,prepare,snapshot


class WritingResultsTests(unittest.TestCase):
    def test_isolated_edits_include_new_files_and_leave_original_dirty_state_intact(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);repo=root/'repo';repo.mkdir();journal=Journal(root/'state')
            git(repo,'init','-q');(repo/'existing.py').write_text('BASE = 1\n');git(repo,'add','existing.py');git(repo,'commit','-qm','Create writing fixture')
            (repo/'existing.py').write_text('BASE = 2\n');(repo/'unrelated.txt').write_text('private original untracked state\n')
            baseline=snapshot(repo);worker=new_id()
            with patch('nativepeers.worktrees.ROOT',root/'project'):
                directory,_,observed=prepare(journal,repo,worker,{'mode':'isolated-worktree'},expected_baseline=baseline)
            work=Path(directory);self.assertEqual(work.parent,(repo/'.worktrees').resolve());self.assertEqual(observed,baseline)
            self.assertEqual((work/'existing.py').read_text(),'BASE = 2\n');self.assertFalse((work/'unrelated.txt').exists())
            (work/'created.py').write_text('def answer():\n    return 42\n');(work/'.env').write_text('PRIVATE_FIXTURE_DO_NOT_COPY=secret\n')
            result=changes(work,baseline)
            self.assertIn('created.py',result['untracked_files']);self.assertIn('.env',result['excluded_sensitive_or_symlink_files'])
            self.assertIn(b'def answer()',result['patch']);self.assertNotIn(b'PRIVATE_FIXTURE',result['patch'])
            copy=repo/'.worktrees/verify-patch';git(repo,'worktree','add','--detach',str(copy),baseline['head'])
            git(copy,'apply','--binary',input=result['patch'])
            self.assertEqual((copy/'created.py').read_bytes(),(work/'created.py').read_bytes())
            self.assertEqual((copy/'existing.py').read_bytes(),(work/'existing.py').read_bytes())
            self.assertEqual(snapshot(repo),baseline);self.assertEqual((repo/'unrelated.txt').read_text(),'private original untracked state\n')
            journal.close()
