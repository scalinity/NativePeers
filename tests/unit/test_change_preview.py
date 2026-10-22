"""Offline patch previews use real Git and never change the source or its index."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from nativepeers.artifacts import Artifacts
from nativepeers.caller import Caller
from nativepeers.change_preview import preview
from nativepeers.errors import PeerError
from nativepeers.files import canonical
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.registry import Registry
from nativepeers.worktrees import changes,git,prepare


class PreviewTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name).resolve();self.source=self.root/'source';self.source.mkdir()
        self.j=Journal(self.root/'state');self.addCleanup(self.j.close)
        parent=Registry(self.j).register('codex',self.root/'home',new_id(),'fixture',identity(os.getpid()).json(),str(self.source),{},owner_kind='test')
        self.caller=Caller('peer',parent['handle'],parent['incarnation_id'])
        self.enterContext(patch('nativepeers.worktrees.ROOT',self.root/'project'))

    def worker(self,copy=False,dirty=False):
        for name in ('edit','parent','deleted'):(self.source/name).write_text('base\n')
        (self.source/'binary').write_bytes(b'\0before\n')
        if not copy:
            git(self.source,'init','-q');git(self.source,'add','.');git(self.source,'commit','-qm','Create preview fixture')
            if dirty:(self.source/'parent').write_text('inherited dirty\n');git(self.source,'add','parent')
        worker=new_id();mode='isolated-copy' if copy else 'isolated-worktree'
        workspace={'mode':mode,**({'file_allowlist':['edit','parent','deleted','binary']} if copy else {})}
        directory,_,baseline=prepare(self.j,self.source,worker,workspace)
        group=new_id();lease={'parent_key':self.caller.key,'baseline':baseline,'spec':{'cwd':directory}}
        self.j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(group,self.caller.key,'{}','COMPLETED','{}'))
        self.j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(worker,group,None,'codex',new_id(),'fixture','fixture','fixture',None,directory,worker,'RELEASED_RESUMABLE',1,canonical(lease).decode()))
        return worker,Path(directory),baseline

    def result(self,worker,work,baseline,**overrides):
        captured=changes(work,baseline);delta=captured.pop('worker_delta');full=captured.pop('patch')
        arts=Artifacts(self.j);task=new_id()
        ch=dict(captured,worktree=str(work),baseline=baseline,worker_delta_empty=not delta and not captured.get('worker_delta_unavailable_reason'),
            worker_delta_artifact=arts.put(delta,self.caller.key) if delta else None,
            patch_artifact=arts.put(full,self.caller.key) if full else None)
        ch.update(overrides)
        row=self.j.one('SELECT * FROM workers WHERE worker_id=?',(worker,))
        record={'task_id':task,'group_id':row['group_id'],'worker':{'native_session_id':row['native_id']},'changes':ch}
        handle=arts.put(canonical(record),self.caller.key)
        self.j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(task,worker,None,None,'COMPLETED',handle,None,row['generation'],'{}'))
        return task,handle

    def source_state(self):
        files={str(p.relative_to(self.source)):p.read_bytes() for p in self.source.rglob('*')
            if p.is_file() and '.worktrees' not in p.parts and '.git' not in p.parts}
        index=self.source/'.git/index'
        return files,index.read_bytes() if index.exists() else None

    def test_worker_only_delta_allows_unrelated_current_edits_without_doubling_dirty_input(self):
        worker,work,base=self.worker(dirty=True);(work/'edit').write_text('worker\n')
        self.result(worker,work,base)
        (self.source/'parent').write_text('unrelated current edit\n');(self.source/'unrelated').write_text('new source file\n')
        before=self.source_state();value=preview(self.j,self.caller,worker)
        self.assertEqual(value['status'],'applicable',value);self.assertTrue(value['applicable'])
        self.assertEqual([f['path'] for f in value['files']],['edit'])
        self.assertEqual(value['worker_delta_base'],base['input_tree']);self.assertFalse(value['applied'])
        self.assertEqual(self.source_state(),before)

    def test_git_and_copy_handle_new_deleted_and_binary_files(self):
        for copy in (False,True):
            with self.subTest(copy=copy):
                # Each variant uses an independent source directory.
                self.source=self.root/('copy' if copy else 'git');self.source.mkdir()
                worker,work,base=self.worker(copy=copy)
                (work/'deleted').unlink();(work/'new file').write_text('new\n');(work/'binary').write_bytes(b'\0after\n')
                self.result(worker,work,base);before=self.source_state()
                value=preview(self.j,self.caller,worker)
                self.assertEqual(value['status'],'applicable',value)
                files={f['path']:f for f in value['files']}
                self.assertEqual(set(files),{'binary','deleted','new file'});self.assertTrue(files['binary']['binary'])
                self.assertIsNone(files['new file']['expected']);self.assertIsNone(files['new file']['current'])
                self.assertEqual(value['workspace_mode'],'isolated-copy' if copy else 'isolated-worktree')
                self.assertEqual(self.source_state(),before)
                if copy:self.assertFalse((self.source/'.git').exists())

    def test_source_conflict_and_new_file_collision_are_reported_without_writes(self):
        worker,work,base=self.worker();(work/'edit').write_text('worker\n');(work/'new').write_text('worker new\n')
        self.result(worker,work,base);(self.source/'edit').write_text('source conflict\n');(self.source/'new').write_text('source new\n')
        before=self.source_state();value=preview(self.j,self.caller,worker)
        self.assertEqual(value['status'],'conflict',value);self.assertFalse(value['applicable']);self.assertFalse(value['inputs_match'])
        self.assertEqual({f['path'] for f in value['files'] if not f['matches_input']},{'edit','new'})
        self.assertEqual(self.source_state(),before)

    def test_renames_compare_both_old_and_new_paths(self):
        worker,work,base=self.worker();git(work,'mv','edit','renamed')
        self.result(worker,work,base);value=preview(self.j,self.caller,worker)
        self.assertEqual(value['status'],'applicable',value)
        self.assertEqual({f['path'] for f in value['files']},{'edit','renamed'})

    def test_noop_and_unavailable_never_substitute_reconstruction_patch(self):
        worker,work,base=self.worker(dirty=True);self.result(worker,work,base)
        value=preview(self.j,self.caller,worker);self.assertEqual(value['status'],'no_changes',value)
        (work/'edit').write_text('worker\n')
        self.result(worker,work,base,worker_delta_artifact=None,worker_delta_empty=False)
        value=preview(self.j,self.caller,worker);self.assertEqual(value['status'],'unavailable');self.assertIn('not substituted',value['reason'])

    def test_requires_verified_owning_parent_and_current_generation_result(self):
        worker,work,base=self.worker();self.result(worker,work,base)
        for caller in (Caller('external-local','external'),Caller('peer','other','other'),
                       Caller('peer',self.caller.key,self.caller.incarnation,{'worker_id':'other'})):
            with self.subTest(caller=caller),self.assertRaises(PeerError):preview(self.j,caller,worker)
        self.j.db.execute('UPDATE workers SET generation=2 WHERE worker_id=?',(worker,))
        with self.assertRaises(PeerError):preview(self.j,self.caller,worker)

    def test_symlinks_filters_and_missing_base_are_unavailable(self):
        worker,work,base=self.worker();(work/'edit').write_text('worker\n');self.result(worker,work,base)
        (self.source/'edit').unlink();(self.source/'edit').symlink_to(self.source/'parent')
        self.assertEqual(preview(self.j,self.caller,worker)['status'],'unavailable')
        (self.source/'edit').unlink();(self.source/'edit').write_text('base\n')
        marker=self.root/'filter-executed'
        (self.source/'.gitattributes').write_text('edit filter=fixture\n')
        git(self.source,'config','filter.fixture.clean','touch '+str(marker))
        self.assertEqual(preview(self.j,self.caller,worker)['status'],'unavailable');self.assertFalse(marker.exists())

    def test_real_harmless_git_process_timeout_is_bounded(self):
        import time
        worker,work,base=self.worker();(work/'edit').write_text('worker\n');self.result(worker,work,base)
        binary=self.root/'bin';binary.mkdir();script=binary/'git'
        script.write_text('#!/bin/sh\nexec /bin/sleep 2\n');script.chmod(0o700)
        start=time.monotonic()
        with patch.dict(os.environ,{'PATH':str(binary)+os.pathsep+os.environ['PATH']}):
            value=preview(self.j,self.caller,worker,timeout=.1)
        self.assertEqual(value['status'],'unavailable',value);self.assertLess(time.monotonic()-start,1)

    def test_source_or_generation_changes_during_check_invalidate_preview(self):
        from nativepeers import change_preview
        worker,work,base=self.worker();(work/'edit').write_text('worker\n');self.result(worker,work,base)
        run=change_preview._git
        def race(cwd,args,*a,**kw):
            value=run(cwd,args,*a,**kw)
            if '--check' in args:self.j.db.execute('UPDATE workers SET generation=2 WHERE worker_id=?',(worker,))
            return value
        with patch('nativepeers.change_preview._git',side_effect=race):
            value=preview(self.j,self.caller,worker)
        self.assertEqual(value['status'],'unavailable',value)
