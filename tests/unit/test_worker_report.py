import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock,patch

from nativepeers.files import canonical
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.worker_process import Worker
from nativepeers.worker_report import extract


def recursive_report():
    depth=sys.getrecursionlimit()+100
    return 'Successful raw final answer\n```nativepeers-result\n{"findings":'+('['*depth)+'0'+(']'*depth)+'}\n```'


class WorkerReportTests(unittest.TestCase):
    def report(self,value):return 'raw findings retained\n```nativepeers-result\n'+json.dumps(value)+'\n```'

    def test_valid_report_is_explicitly_unverified(self):
        raw=self.report({'findings':[{'severity':'warning','summary':'race','file':'a.py','line':4}],
                         'tests_run':[{'command':'pytest -q','outcome':'passed','details':'2 passed'}]})
        findings,checks,limitations=extract(raw)
        self.assertEqual(findings[0]['summary'],'race');self.assertEqual(checks[0]['command'],'pytest -q')
        for item in findings+checks:
            self.assertEqual(item['source'],'worker-reported');self.assertFalse(item['independently_verified'])
        self.assertEqual(limitations,[]);self.assertIn('raw findings retained',raw)

    def test_malformed_partial_and_unsupported_claims_keep_useful_fields(self):
        for text in ['plain output','```nativepeers-result\nnot JSON','```nativepeers-result\n'+('x'*16385),self.report([])]:
            with self.subTest(text=text[:30]):
                findings,checks,limitations=extract(text);self.assertFalse(findings or checks);self.assertTrue(limitations)
        raw=self.report({'findings':[{'severity':'info','summary':'retained'},{'severity':'invented','summary':'bad'}],
                         'tests_run':[{'command':'pytest','outcome':'passed','independently_verified':True},{'command':'lint','outcome':'not_run'}]})
        findings,checks,limitations=extract(raw)
        self.assertEqual(len(findings),1);self.assertEqual(len(checks),1)
        self.assertEqual(checks[0]['outcome'],'not_run');self.assertGreaterEqual(len(limitations),2)

    def test_recursive_json_below_size_limit_is_an_optional_report_limitation(self):
        raw=recursive_report();self.assertLess(len(raw.encode()),16384)
        # The 3.14 C decoder accepts this depth; the stdlib Python decoder still exercises its recursion failure.
        decoder=json.JSONDecoder();decoder.scan_once=json.scanner.py_make_scanner(decoder)
        with patch('nativepeers.worker_report.json.loads',side_effect=decoder.decode):
            findings,checks,limitations=extract(raw)
        self.assertEqual((findings,checks),([],[]))
        self.assertEqual(limitations,['Malformed structured worker report; provider final text retained.'])


class WorkerReportRetentionTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_task_retains_raw_final_after_recursive_optional_report(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=Journal(Path(directory));self.addCleanup(journal.close)
            group,worker_id,task_id,native=[new_id() for _ in range(4)]
            spec={'provider':'claude','model':'sonnet','permission_profile':'analysis','max_task_turns':1,'wall_time_seconds':5}
            lease={'provider_home':str(journal.root.resolve()),'spec':spec,'binding':str(journal.root/'bindings'/worker_id),'parent_key':'parent'}
            journal.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(group,'parent','{}','RUNNING','{}'))
            journal.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (worker_id,group,'peer','claude',native,'report fixture','sonnet',None,None,directory,None,'RUNNING',1,canonical(lease).decode()))
            journal.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(task_id,worker_id,None,None,'QUEUED',None,None,1,'{}'))
            worker=Worker(journal,worker_id);raw=recursive_report()
            decoder=json.JSONDecoder();decoder.scan_once=json.scanner.py_make_scanner(decoder)
            with patch.object(worker,'claude_turn',new_callable=AsyncMock,return_value={'status':'completed','text':raw,'native_turn_ids':[native]}), \
                 patch('nativepeers.worker_report.json.loads',side_effect=decoder.decode), \
                 patch.object(worker,'emit'):
                await worker.task(task_id,'fixture')
            task=journal.one('SELECT * FROM tasks WHERE task_id=?',(task_id,))
            self.assertEqual(task['state'],'COMPLETED');self.assertIsNotNone(task['result_artifact_id'])
            result=json.loads((journal.root/'artifacts'/task['result_artifact_id']).read_text())
            self.assertEqual(result['status'],'completed');self.assertEqual(result['summary'],raw)
            self.assertEqual(result['findings'],[]);self.assertEqual(result['verification']['tests_run'],[])
            self.assertIn('Malformed structured worker report; provider final text retained.',result['verification']['limitations'])
            self.assertEqual(journal.one('SELECT state FROM workers')['state'],'IDLE_GRACE')
            self.assertIsNone(worker.running_task);self.assertFalse(worker.retire_after_task)
