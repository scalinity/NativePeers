"""Planner-only cleanup with real repositories, stopped processes and socket guards."""
import json
import asyncio
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from nativepeers.artifacts import Artifacts
from nativepeers.caller import Caller
from nativepeers.cleanup import plan
from nativepeers.errors import PeerError
from nativepeers.files import atomic_json,canonical
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.registry import Registry
from nativepeers.worktrees import changes,git,prepare


class CleanupTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name).resolve();self.source=self.root/'source';self.source.mkdir()
        self.j=Journal(self.root/'state');self.addCleanup(self.j.close)
        peer=Registry(self.j).register('codex',self.root/'home',new_id(),'fixture parent',identity(os.getpid()).json(),str(self.source),{},owner_kind='test')
        self.caller=Caller('peer',peer['handle'],peer['incarnation_id'])
        self.enterContext(patch('nativepeers.worktrees.ROOT',self.root/'project'))
        process=subprocess.Popen(['/bin/sleep','.1']);self.dead=identity(process.pid).json();process.wait()

    def worker(self,copy=False,dirty=False,result_change=False):
        (self.source/'input').write_text('base\n')
        if not copy:
            git(self.source,'init','-q');git(self.source,'add','input');git(self.source,'commit','-qm','Create cleanup fixture')
            if dirty:(self.source/'input').write_text('inherited dirty\n')
        worker=new_id();group=new_id();native=new_id();task=new_id()
        mode='isolated-copy' if copy else 'isolated-worktree'
        directory,_,baseline=prepare(self.j,self.source,worker,{'mode':mode,**({'file_allowlist':['input']} if copy else {})})
        work=Path(directory)
        lease={'parent_key':self.caller.key,'baseline':baseline,'spec':{'cwd':directory}}
        self.j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(group,self.caller.key,'{}','COMPLETED','{}'))
        self.j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(worker,group,None,'codex',native,'fixture','fixture','fixture',None,directory,worker,'RELEASED_RESUMABLE',1,canonical(lease).decode()))
        if result_change:(work/'input').write_text('unique worker edit\n')
        captured=changes(work,baseline);delta=captured.pop('worker_delta');full=captured.pop('patch');arts=Artifacts(self.j)
        ch=dict(captured,baseline=baseline,worktree=directory,worker_delta_empty=not delta,
                worker_delta_artifact=arts.put(delta,self.caller.key) if delta else None,
                patch_artifact=arts.put(full,self.caller.key) if full else None)
        record={'task_id':task,'group_id':group,'worker':{'native_session_id':native},'changes':ch}
        result=arts.put(canonical(record),self.caller.key)
        self.j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(task,worker,None,None,'COMPLETED',result,None,1,'{}'))
        atomic_json(self.j.root/'workers'/(worker+'.json'),{'worker_id':worker,'group_id':group,'native_id':native,
            'generation':1,'owner':self.dead,'native_process':None,'cleanup':{'state':'CLOSED','output_eof':True}})
        return worker,work,baseline

    def candidate(self,worker,**kw):return plan(self.j,self.caller,[worker],**kw)['candidates'][0]

    def test_cli_auto_selection_and_exact_worker_plan_preserve_scope_and_content(self):
        from nativepeers.cli import parser,dispatch
        worker,work,base=self.worker();before=self.j.db.total_changes
        with patch('nativepeers.cli.bind_caller',return_value=self.caller):
            automatic=asyncio.run(dispatch(parser().parse_args(['cleanup','--plan','--limit','1','--json']),self.j))
            selected=asyncio.run(dispatch(parser().parse_args(['cleanup','--plan','--worker-id',worker]),self.j))
        self.assertEqual(automatic,selected);self.assertTrue(selected['candidates'][0]['safe_candidate'])
        self.assertTrue(work.exists());self.assertEqual(self.j.db.total_changes,before)
        self.j.db.execute("UPDATE groups SET parent_key='different-parent'")
        with patch('nativepeers.cli.bind_caller',return_value=self.caller):
            empty=asyncio.run(dispatch(parser().parse_args(['cleanup','--plan']),self.j))
            self.assertEqual(empty['candidates'],[])
            with self.assertRaises(PeerError) as error:asyncio.run(dispatch(parser().parse_args(['cleanup','--plan','--worker-id',worker]),self.j))
        self.assertEqual(error.exception.code,'APPROVAL_BLOCKED');self.assertTrue(work.exists())

    def test_git_nochange_candidate_uses_worktree_remove_without_mutating_anything(self):
        worker,work,base=self.worker();index=(self.source/'.git/index').read_bytes()
        before=self.j.db.total_changes;artifacts={p.name:p.read_bytes() for p in (self.j.root/'artifacts').iterdir()}
        result=plan(self.j,self.caller,[worker]);value=result['candidates'][0]
        self.assertTrue(value['safe_candidate'],value);self.assertTrue(value['size_complete']);self.assertGreater(value['size_bytes'],0)
        self.assertEqual(value['procedure']['kind'],'git-worktree-remove')
        self.assertEqual(value['procedure']['argv'],['git','-C',str(self.source),'worktree','remove',str(work)])
        self.assertTrue(value['current_changes']['matches_input_tree']);self.assertTrue(value['retained_result']['result_artifact_id'])
        self.assertTrue(result['planner_only']);self.assertFalse(result['removed']);self.assertTrue(result['recheck_required'])
        self.assertEqual(len(value['retained_results']),1)
        self.assertTrue(work.exists());self.assertEqual((self.source/'.git/index').read_bytes(),index)
        self.assertEqual(self.j.db.total_changes,before);self.assertEqual({p.name:p.read_bytes() for p in (self.j.root/'artifacts').iterdir()},artifacts)

    def test_copy_nochange_candidate_uses_manual_selected_copy_removal(self):
        worker,work,base=self.worker(copy=True);value=self.candidate(worker)
        self.assertTrue(value['safe_candidate'],value)
        self.assertEqual(value['procedure']['kind'],'selected-copy-manual-removal');self.assertEqual(value['procedure']['path'],str(work))
        self.assertNotIn('argv',value['procedure']);self.assertFalse((self.source/'.git').exists());self.assertTrue(work.exists())

    def test_retained_unique_delta_refuses_even_when_current_files_are_reverted(self):
        worker,work,base=self.worker(copy=True,result_change=True);(work/'input').write_text('base\n')
        value=self.candidate(worker)
        self.assertFalse(value['safe_candidate']);self.assertTrue(value['unique_changes'],value)
        self.assertTrue(value['retained_result']['worker_delta_artifact']);self.assertIsNone(value['procedure'])

    def test_fresh_edits_ignored_files_deletions_binary_and_staged_only_changes_refuse(self):
        worker,work,base=self.worker(copy=True)
        (work/'input').write_text('fresh edit\n');self.assertTrue(self.candidate(worker)['unique_changes'])
        (work/'input').write_text('base\n');(work/'new.bin').write_bytes(b'\0unique')
        self.assertTrue(self.candidate(worker)['unique_changes']);(work/'new.bin').unlink()
        (work/'.git/info/exclude').write_text('ignored\n');(work/'ignored').write_text('unique ignored content\n')
        self.assertTrue(self.candidate(worker)['unique_changes']);(work/'ignored').unlink()
        (work/'input').unlink();self.assertTrue(self.candidate(worker)['unique_changes'])
        (work/'input').write_text('staged unique\n');git(work,'add','input');(work/'input').write_text('base\n')
        self.assertTrue(self.candidate(worker)['unique_changes'])

    def test_unique_commits_refs_and_dangling_objects_refuse(self):
        worker,work,base=self.worker(copy=True)
        (work/'input').write_text('commit\n');git(work,'add','input');git(work,'commit','-qm','Retain unique fixture')
        self.assertTrue(self.candidate(worker)['unique_changes'])
        git(work,'reset','--hard',base['head']);self.assertTrue(self.candidate(worker)['unique_changes'])
        # Even an unreferenced blob in a selected copy is retained, not discarded.
        git(work,'reflog','expire','--expire=now','--all');git(work,'hash-object','-w','--stdin',input=b'loose fixture')
        self.assertTrue(self.candidate(worker)['unique_changes'])

    def test_active_and_resumed_generations_and_live_processes_refuse(self):
        worker,work,base=self.worker(copy=True)
        self.j.db.execute("UPDATE workers SET state='RUNNING' WHERE worker_id=?",(worker,))
        self.assertFalse(self.candidate(worker)['safe_candidate'])
        self.j.db.execute("UPDATE workers SET state='RELEASED_RESUMABLE',generation=2 WHERE worker_id=?",(worker,))
        self.assertIn('Resumed',self.candidate(worker)['reason'])
        self.j.db.execute('UPDATE workers SET generation=1 WHERE worker_id=?',(worker,))
        path=self.j.root/'workers'/(worker+'.json');manifest=json.loads(path.read_bytes());manifest['owner']=identity(os.getpid()).json();atomic_json(path,manifest)
        self.assertEqual(self.candidate(worker)['reason_code'],'CLEANUP_UNCERTAIN')

    def test_unknown_manifest_ownership_and_exact_worker_ids_are_guarded(self):
        worker,work,base=self.worker(copy=True)
        self.j.db.execute("UPDATE groups SET parent_key='other' WHERE group_id=(SELECT group_id FROM workers WHERE worker_id=?)",(worker,))
        with self.assertRaises(PeerError):self.candidate(worker)
        with self.assertRaises(PeerError):plan(self.j,Caller('external-local','external'),[worker])
        with self.assertRaises(PeerError):plan(self.j,self.caller,['../unrelated'])
        with self.assertRaises(PeerError):plan(self.j,self.caller,[worker,worker])
        with self.assertRaises(PeerError):plan(self.j,self.caller,['fixture title'])
        with self.assertRaises(PeerError):plan(self.j,self.caller,[{}])

    def test_unrelated_directory_replacement_and_symlinks_are_refused(self):
        worker,work,base=self.worker();(work/'.git').unlink();git(work,'init','-q')
        value=self.candidate(worker);self.assertFalse(value['safe_candidate']);self.assertTrue(work.exists())
        # The exact recorded path cannot be redirected at an unrelated directory.
        target=self.root/'unrelated';target.mkdir()
        self.j.db.execute('UPDATE workers SET cwd=? WHERE worker_id=?',(str(target),worker))
        self.assertFalse(self.candidate(worker)['safe_candidate'])

    def test_real_socket_and_symlink_content_never_becomes_candidate(self):
        worker,work,base=self.worker(copy=True)
        sock=socket.socket(socket.AF_UNIX);self.addCleanup(sock.close)
        # Darwin limits Unix socket paths to 104 bytes; bind relative to the fixture.
        original=Path.cwd()
        try:os.chdir(work);sock.bind('local.sock')
        finally:os.chdir(original)
        value=self.candidate(worker);self.assertFalse(value['safe_candidate']);self.assertIn('socket',value['reason'])
        sock.close();(work/'local.sock').unlink();(work/'link').symlink_to(self.source/'input')
        self.assertFalse(self.candidate(worker)['safe_candidate'])

    def test_bounds_and_current_source_inputs_are_rechecked(self):
        worker,work,base=self.worker(copy=True)
        small=self.candidate(worker,max_entries=1);self.assertFalse(small['safe_candidate']);self.assertFalse(small['size_complete'])
        small=self.candidate(worker,max_bytes=1);self.assertFalse(small['safe_candidate']);self.assertFalse(small['size_complete'])
        (self.source/'input').write_text('source moved on\n')
        value=self.candidate(worker);self.assertFalse(value['safe_candidate']);self.assertIn('source',value['reason'])

    def test_generation_or_content_race_cancels_candidate_and_does_not_delete(self):
        from nativepeers import cleanup
        worker,work,base=self.worker(copy=True);inventory=cleanup._inventory;calls=0
        def race(*args,**kwargs):
            nonlocal calls
            value=inventory(*args,**kwargs);calls+=1
            if calls==2:self.j.db.execute('UPDATE workers SET generation=2 WHERE worker_id=?',(worker,))
            return value
        with patch('nativepeers.cleanup._inventory',side_effect=race):value=self.candidate(worker)
        self.assertFalse(value['safe_candidate']);self.assertTrue(work.exists())

    def test_inherited_dirty_git_input_is_retained_without_force_procedure(self):
        worker,work,base=self.worker(dirty=True);value=self.candidate(worker)
        self.assertFalse(value['safe_candidate']);self.assertIn('dirty',value['reason']);self.assertIsNone(value['procedure'])

    def test_missing_or_stale_process_manifest_is_never_inferred_as_released(self):
        worker,work,base=self.worker(copy=True);path=self.j.root/'workers'/(worker+'.json')
        raw=path.read_bytes();path.unlink()
        self.assertFalse(self.candidate(worker)['safe_candidate'])
        manifest=json.loads(raw);manifest['generation']=99;atomic_json(path,manifest)
        value=self.candidate(worker);self.assertFalse(value['safe_candidate']);self.assertEqual(value['reason_code'],'OWNERSHIP_UNKNOWN')

    def test_content_change_during_second_inventory_is_refused(self):
        from nativepeers import cleanup
        worker,work,base=self.worker(copy=True);inventory=cleanup._inventory;calls=0
        def race(*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==2:(work/'input').write_text('late edit\n')
            return inventory(*args,**kwargs)
        with patch('nativepeers.cleanup._inventory',side_effect=race):value=self.candidate(worker)
        self.assertFalse(value['safe_candidate']);self.assertIn('changed during',value['reason']);self.assertTrue(work.exists())

    def test_positive_prelaunch_failure_has_a_read_only_cleanup_candidate_without_manifest(self):
        from nativepeers.delegation import Delegation
        from nativepeers.errors import SupervisorNotStarted
        (self.source/'input').write_text('base\n');git(self.source,'init','-q');git(self.source,'add','input');git(self.source,'commit','-qm','Create never-launched fixture')
        parent=self.j.one('SELECT * FROM peer_instances WHERE peer_key=?',(self.caller.key,))
        descriptor=json.loads(parent['descriptor_json']);descriptor['policy']={'profiles':['analysis','workspace']}
        self.j.db.execute('UPDATE peer_instances SET descriptor_json=? WHERE peer_key=?',(canonical(descriptor).decode(),self.caller.key))
        home=self.root/'home';binary=home/'.local/bin/claude';binary.parent.mkdir(parents=True);binary.write_text('never invoked')
        async def failure(group):raise SupervisorNotStarted(group,FileNotFoundError('positive fixture prelaunch failure'))
        request={'workers':[{'provider':'claude','title':'never started','task':'unused','permission_profile':'workspace','workspace':{'mode':'isolated-worktree'}}]}
        with patch('nativepeers.delegation.Path.home',return_value=home),patch.dict(os.environ,{'CLAUDE_CONFIG_DIR':str(home/'.claude')}),patch.object(Delegation,'ensure_supervisor',side_effect=failure),self.assertRaises(SupervisorNotStarted):
            asyncio.run(Delegation(self.j,Registry(self.j)).spawn(self.caller,request))
        row=self.j.one('SELECT * FROM workers');self.assertEqual(row['state'],'FAILED')
        self.assertFalse((self.j.root/'workers'/(row['worker_id']+'.json')).exists())
        before=self.j.db.total_changes;value=self.candidate(row['worker_id'])
        self.assertTrue(value['safe_candidate'],value);self.assertTrue(value['never_launched']);self.assertIsNone(value['retained_result'])
        self.assertEqual(self.j.db.total_changes,before);self.assertTrue(Path(row['cwd']).exists())
        (Path(row['cwd'])/'input').write_text('unique later edit\n')
        self.assertFalse(self.candidate(row['worker_id'])['safe_candidate'])
        self.j.db.execute('DELETE FROM events WHERE kind=\'controller-not-created\'')
        self.assertFalse(self.candidate(row['worker_id'])['safe_candidate'])
