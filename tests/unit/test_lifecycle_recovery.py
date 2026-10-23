import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from nativepeers.caller import Caller
from nativepeers.files import atomic_json,canonical
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.recovery import reconcile_owned


class LifecycleRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.j=Journal(Path(self.temp.name));self.addCleanup(self.j.close)
        self.group,self.worker,self.running,self.queued,self.done=[new_id() for _ in range(5)]
        self.caller=Caller('peer','parent',new_id())
        self.j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(self.group,'parent',canonical({'initial_tasks':[self.running]}).decode(),'PARTIAL','{}'))
        self.j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(self.worker,self.group,None,'codex',new_id(),'offline worker','gpt-6.1-sol',None,None,self.temp.name,None,'RELEASED_RESUMABLE',1,'{}'))
        for task,state,turn,artifact,reason in [(self.running,'RUNNING','submitted-turn',None,None),(self.queued,'QUEUED',None,None,None),(self.done,'COMPLETED','finished-turn','retained-artifact','retained-reason')]:
            self.j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(task,self.worker,None,turn,state,artifact,reason,1,'{}'))
        owner=identity(os.getpid()).json()
        self.dead=dict(owner,pid=99999999,pgid=99999999)
        self.manifest=dict(worker_id=self.worker,native_id=self.j.one('SELECT native_id FROM workers')['native_id'],generation=1,owner=self.dead,native_process=None,cleanup={'state':'CLOSED','output_eof':True})
        self.path=self.j.root/'workers'/(self.worker+'.json');atomic_json(self.path,self.manifest)

    def tasks(self):return self.j.all('SELECT * FROM tasks ORDER BY task_id')

    def test_released_worker_unfinished_tasks_are_reconciled_once_without_replay(self):
        before=self.tasks();self.assertEqual(len(reconcile_owned(self.j,self.caller,self.group,dry_run=True)),1)
        self.assertEqual(self.tasks(),before)
        self.assertEqual(len(reconcile_owned(self.j,self.caller,self.group)),1)
        running=self.j.one('SELECT * FROM tasks WHERE task_id=?',(self.running,))
        queued=self.j.one('SELECT * FROM tasks WHERE task_id=?',(self.queued,))
        self.assertEqual((running['state'],running['terminal_reason'],running['native_turn_id']),('INTERRUPTED','DELIVERY_UNKNOWN_AFTER_OWNER_LOSS','submitted-turn'))
        self.assertEqual((queued['state'],queued['terminal_reason']),('CANCELED','OWNER_LOST_BEFORE_START'))
        self.assertEqual(next(t for t in before if t['task_id']==self.done),self.j.one('SELECT * FROM tasks WHERE task_id=?',(self.done,)))
        settled=self.tasks();events=self.j.all('SELECT * FROM events')
        self.assertEqual(reconcile_owned(self.j,self.caller,self.group),[])
        self.assertEqual(self.tasks(),settled);self.assertEqual(self.j.all('SELECT * FROM events'),events)
        self.assertEqual(self.j.all('SELECT * FROM send_requests'),[])

    def test_released_worker_requires_both_exact_writers_dead(self):
        process=subprocess.Popen([sys.executable,'-c',"import time;print('ready',flush=True);time.sleep(60)"],stdout=subprocess.PIPE,text=True)
        try:
            self.assertEqual(process.stdout.readline(),'ready\n')
            live=identity(process.pid).json();before=self.tasks()
            for field in ['owner','native_process']:
                with self.subTest(field=field):
                    atomic_json(self.path,dict(self.manifest,**{field:live}))
                    self.assertEqual(reconcile_owned(self.j,self.caller,self.group),[])
                    self.assertEqual(self.tasks(),before)
            process.terminate();process.wait(timeout=5)
            self.assertEqual(len(reconcile_owned(self.j,self.caller,self.group)),1)
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=5)
            process.stdout.close()

    def test_missing_or_mismatched_manifests_cannot_settle_tasks(self):
        before=self.tasks();self.path.unlink()
        self.assertEqual(reconcile_owned(self.j,self.caller,self.group),[])
        for override in [{'worker_id':new_id()},{'native_id':new_id()},{'generation':2}]:
            with self.subTest(override=override):
                atomic_json(self.path,dict(self.manifest,**override))
                self.assertEqual(reconcile_owned(self.j,self.caller,self.group),[])
                self.assertEqual(self.tasks(),before)

    def test_queued_record_with_native_turn_is_not_proven_unstarted(self):
        self.j.db.execute("UPDATE tasks SET native_turn_id='possibly-submitted' WHERE task_id=?",(self.queued,))
        reconcile_owned(self.j,self.caller,self.group)
        task=self.j.one('SELECT * FROM tasks WHERE task_id=?',(self.queued,))
        self.assertEqual((task['state'],task['terminal_reason'],task['native_turn_id']),('INTERRUPTED','DELIVERY_UNKNOWN_AFTER_OWNER_LOSS','possibly-submitted'))

    def test_exec_or_session_transition_is_not_release_evidence(self):
        import time
        from nativepeers.process import owned_status, matches, signal_captured
        from nativepeers.recovery import assert_released
        from nativepeers.errors import PeerError
        for transition in ['os.setsid(); print("changed",flush=True); time.sleep(30)',
                           'os.execv("/bin/sleep",["sleep","30"])']:
            with self.subTest(transition=transition):
                process=subprocess.Popen([sys.executable,'-c',
                    'import os,sys,time; print("ready",flush=True); sys.stdin.readline(); '+transition],
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
                try:
                    self.assertEqual(process.stdout.readline(),'ready\n')
                    birth=identity(process.pid).json()
                    process.stdin.write('go\n');process.stdin.flush()
                    deadline=time.monotonic()+3
                    while matches(birth) and time.monotonic()<deadline:time.sleep(.01)
                    self.assertFalse(matches(birth));self.assertEqual(owned_status(birth),'LIVE')
                    atomic_json(self.path,dict(self.manifest,native_process=birth))
                    self.assertEqual(reconcile_owned(self.j,self.caller,self.group),[])
                    with self.assertRaises(PeerError):assert_released(self.j,self.j.one('SELECT * FROM workers'))
                    self.assertTrue(signal_captured(birth,9));process.wait(timeout=3)
                    self.assertEqual(owned_status(birth),'DEAD')
                finally:
                    if process.poll() is None:process.kill();process.wait(timeout=3)
                    process.stdin.close();process.stdout.close()

    def test_missing_positive_closure_stays_unknown_and_explains_why(self):
        from nativepeers.recovery import resume_command
        manifest=dict(self.manifest);manifest.pop('cleanup');atomic_json(self.path,manifest)
        self.assertEqual(reconcile_owned(self.j,self.caller,self.group),[])
        report=resume_command(self.j,self.caller,self.worker,explain=True)
        self.assertFalse(report['resume_eligible'])
        self.assertIn('EOF',report['closure']['reasons'][0])
