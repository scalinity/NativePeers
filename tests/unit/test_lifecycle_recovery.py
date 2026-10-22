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
        self.manifest=dict(worker_id=self.worker,native_id=self.j.one('SELECT native_id FROM workers')['native_id'],generation=1,owner=self.dead,native_process=None)
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
