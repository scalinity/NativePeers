"""Workspace Git commands must never inherit an unrelated index or object store."""
import os
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nativepeers.errors import PeerError
from nativepeers.journal import Journal
from nativepeers.worktrees import changes,freeze,git,prepare


class GitRoutingTests(unittest.TestCase):
    def test_copy_success_and_refusal_preserve_unrelated_git_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'source';source.mkdir();(source/'input.txt').write_text('selected\n')
            other=root/'other';other.mkdir();(other/'original').write_text('unrelated\n')
            git(other,'init','-q');git(other,'add','original');git(other,'commit','-qm','Create routing fixture')
            def inventory():return {str(p.relative_to(other)):p.read_bytes() for p in (other/'.git').rglob('*') if p.is_file()}
            before=inventory();workspace={'mode':'isolated-copy','file_allowlist':['input.txt']}
            with closing(Journal(root/'state')) as journal,patch.dict(os.environ,{
                    'GIT_INDEX_FILE':str(other/'.git/index'),'GIT_DIR':str(other/'.git'),
                    'GIT_WORK_TREE':str(other),'GIT_OBJECT_DIRECTORY':str(other/'.git/objects'),
                    'GIT_ALTERNATE_OBJECT_DIRECTORIES':str(other/'.git/objects')}):
                frozen=freeze(source,[workspace],journal=journal)
                directory,_,baseline=prepare(journal,source,'copy',workspace,frozen=frozen)
                self.assertEqual(git(directory,'show','HEAD:input.txt').stdout,b'selected\n')
                (Path(directory)/'input.txt').write_text('worker\n')
                self.assertIn(b'+worker',changes(directory,baseline)['worker_delta'])
                (source/'input.txt').write_text('changed\n')
                with self.assertRaises(PeerError):prepare(journal,source,'refused',workspace,frozen=frozen)
                self.assertFalse((source/'.worktrees/refused').exists())
            self.assertEqual(inventory(),before)
