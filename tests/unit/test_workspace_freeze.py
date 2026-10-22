import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from nativepeers.errors import PeerError
from nativepeers.journal import Journal
from nativepeers.worktrees import freeze,git,prepare,snapshot,validate_frozen


class WorkspaceFreezeTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.repo=self.root/'repo';self.repo.mkdir()
        git(self.repo,'init','-q');(self.repo/'tracked.txt').write_text('baseline\n')
        git(self.repo,'add','tracked.txt');git(self.repo,'commit','-qm','Create workspace freeze fixture')
        self.journal=Journal(self.root/'state');self.addCleanup(self.journal.close)
        override=patch('nativepeers.worktrees.ROOT',self.root/'project');override.start();self.addCleanup(override.stop)

    def workspace(self,*selected):
        return {'mode':'isolated-worktree','untracked_allowlist':list(selected)}

    def test_frozen_union_preserves_dirty_source_and_worker_selections(self):
        source=self.repo/'tracked.txt';source.write_text('staged\n');git(self.repo,'add','tracked.txt');source.write_text('unstaged\n')
        script=self.repo/'inputs/run';script.parent.mkdir();script.write_bytes(b'#!/bin/sh\n\x00frozen\n');script.chmod(0o751)
        (self.repo/'second.txt').write_text('second\n');(self.repo/'unselected.txt').write_text('private\n')
        (self.repo/'.env').write_text('FIXTURE_SECRET=not-real\n')
        before=snapshot(self.repo);staged=git(self.repo,'diff','--cached','--binary').stdout
        first=self.workspace('inputs/run');second=self.workspace('inputs/run','second.txt')
        frozen=freeze(self.repo,[first,second],expected_baseline=before)
        self.assertEqual(frozen.baseline['untracked_inputs']['inputs/run'],
                         {'sha256':hashlib.sha256(script.read_bytes()).hexdigest(),'mode':0o751})
        self.assertEqual(json.loads(json.dumps(frozen.baseline)),frozen.baseline)
        for worker,workspace in [('first',first),('second',second)]:
            target,_,baseline=prepare(self.journal,self.repo,worker,workspace,frozen=frozen)
            target=Path(target)
            self.assertEqual({k:v for k,v in baseline.items() if k!='input_tree'},frozen.baseline)
            self.assertEqual(git(target,'show',baseline['input_tree']+':inputs/run').stdout,script.read_bytes())
            self.assertEqual(bool(git(target,'ls-tree',baseline['input_tree'],'--','second.txt').stdout),worker=='second')
            self.assertEqual((target/'tracked.txt').read_text(),'unstaged\n')
            self.assertEqual((target/'inputs/run').read_bytes(),script.read_bytes())
            self.assertEqual(stat.S_IMODE((target/'inputs/run').stat().st_mode),0o751)
            self.assertEqual((target/'second.txt').exists(),worker=='second')
            self.assertFalse((target/'unselected.txt').exists());self.assertFalse((target/'.env').exists())
        validate_frozen(self.repo,frozen)
        self.assertEqual(snapshot(self.repo),before)
        self.assertEqual(git(self.repo,'diff','--cached','--binary').stdout,staged)

    def test_real_git_mutations_between_preparations_reject_bytes_and_modes(self):
        selected=self.repo/'selected';workspace=self.workspace('selected')
        for mutation in ['bytes','mode']:
            with self.subTest(mutation=mutation):
                selected.write_bytes(b'first\n');selected.chmod(0o640)
                before=snapshot(self.repo);frozen=freeze(self.repo,[workspace],expected_baseline=before)
                target,_,_=prepare(self.journal,self.repo,mutation+'-first',workspace,frozen=frozen)
                if mutation=='bytes':selected.write_bytes(b'other\n')
                else:selected.chmod(0o750)
                # Real Git reports the identical path/status and tracked diff in both cases.
                self.assertEqual(snapshot(self.repo),before)
                with self.assertRaises(PeerError) as error:
                    prepare(self.journal,self.repo,mutation+'-second',workspace,frozen=frozen)
                self.assertEqual(error.exception.code,'WORKSPACE_SNAPSHOT_REQUIRED')
                self.assertFalse((self.repo/'.worktrees'/(mutation+'-second')).exists())
                with self.assertRaises(PeerError):validate_frozen(self.repo,frozen)
                self.assertEqual(Path(target,'selected').read_bytes(),b'first\n')
                self.assertEqual(stat.S_IMODE(Path(target,'selected').stat().st_mode),0o640)
                self.assertEqual(selected.read_bytes(),b'other\n' if mutation=='bytes' else b'first\n')

    def test_mutation_during_creation_never_copies_live_source_bytes(self):
        selected=self.repo/'selected';selected.write_bytes(b'frozen\n');selected.chmod(0o751)
        workspace=self.workspace('selected');frozen=freeze(self.repo,[workspace])
        def mutate_after_add(cwd,*args,**kwargs):
            result=git(cwd,*args,**kwargs)
            if args[:2]==('worktree','add'):
                selected.write_bytes(b'live changed\n');selected.chmod(0o600)
            return result
        with patch('nativepeers.worktrees.git',side_effect=mutate_after_add):
            with self.assertRaises(PeerError) as error:
                prepare(self.journal,self.repo,'during-creation',workspace,frozen=frozen)
        self.assertEqual(error.exception.code,'WORKSPACE_SNAPSHOT_REQUIRED')
        retained=self.repo/'.worktrees/during-creation/selected'
        self.assertEqual(retained.read_bytes(),b'frozen\n')
        self.assertEqual(stat.S_IMODE(retained.stat().st_mode),0o751)
        self.assertEqual(selected.read_bytes(),b'live changed\n')

    def test_final_validation_rejects_mutation_after_all_worktrees_are_ready(self):
        selected=self.repo/'selected';selected.write_text('before\n');workspace=self.workspace('selected')
        frozen=freeze(self.repo,[workspace,workspace])
        for worker in ['ready-first','ready-second']:
            prepare(self.journal,self.repo,worker,workspace,frozen=frozen)
        selected.write_text('after\n')
        with self.assertRaises(PeerError) as error:validate_frozen(self.repo,frozen)
        self.assertEqual(error.exception.code,'WORKSPACE_SNAPSHOT_REQUIRED')

    def test_invalid_union_is_rejected_before_worktree_creation(self):
        (self.repo/'selected').write_text('okay\n');(self.repo/'.env').write_text('FIXTURE_SECRET=not-real\n')
        (self.repo/'ignored').write_text('private\n');(self.repo/'.git/info/exclude').write_text('ignored\n')
        (self.repo/'link').symlink_to(self.repo/'selected')
        (self.repo/'linked-directory').symlink_to(self.root,target_is_directory=True)
        (self.repo/'directory').mkdir();os.mkfifo(self.repo/'fifo')
        before=snapshot(self.repo)
        for selected in ['.env','ignored','tracked.txt','../outside',str(self.repo/'selected'),
                         'link','linked-directory/other','directory','fifo','missing']:
            with self.subTest(selected=selected),self.assertRaises(PeerError) as error:
                freeze(self.repo,[self.workspace('selected'),self.workspace(selected)])
            self.assertEqual(error.exception.code,'WORKSPACE_SNAPSHOT_REQUIRED')
        self.assertFalse((self.repo/'.worktrees').exists());self.assertEqual(snapshot(self.repo),before)

    def test_selection_not_in_batch_freeze_is_rejected(self):
        (self.repo/'first').write_text('first\n');(self.repo/'second').write_text('second\n')
        frozen=freeze(self.repo,[self.workspace('first')])
        with self.assertRaises(PeerError) as error:
            prepare(self.journal,self.repo,'unfrozen',self.workspace('second'),frozen=frozen)
        self.assertEqual(error.exception.code,'WORKSPACE_SNAPSHOT_REQUIRED')
        self.assertFalse((self.repo/'.worktrees').exists())

    def test_read_only_freeze_cannot_be_used_for_writing(self):
        for cwd in [self.repo,self.root]:
            with self.subTest(cwd=cwd):
                frozen=freeze(cwd,[{'mode':'inherit-read-only'}])
                with self.assertRaises(PeerError) as error:
                    prepare(self.journal,cwd,'not-frozen',self.workspace(),frozen=frozen)
                self.assertEqual(error.exception.code,'WORKSPACE_SNAPSHOT_REQUIRED')
                self.assertFalse((cwd/'.worktrees').exists())

    def test_dirty_tracked_secrets_are_rejected_without_changing_source(self):
        secret=self.repo/'.env';secret.write_text('FIXTURE=baseline\n');git(self.repo,'add','.env');git(self.repo,'commit','-qm','Track secret exclusion fixture')
        secret.write_text('FIXTURE=dirty\n');before=snapshot(self.repo)
        with self.assertRaises(PeerError):freeze(self.repo,[self.workspace()])
        self.assertEqual(snapshot(self.repo),before);self.assertFalse((self.repo/'.worktrees').exists())
