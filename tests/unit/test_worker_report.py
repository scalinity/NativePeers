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

    def review(self,items,structured=None):
        text='## Code Review: disposable review\n### Summary\nSelected fixture only; shell not run.\n'
        for title,severity in [('🔴 Critical Issues (Must Fix)','critical'),('🟡 Warnings (Should Fix)','warning'),('🔵 Suggestions (Consider)','info')]:
            text+='### '+title+'\n'
            selected=[item for item in items if item['severity']==severity]
            text+='\n'.join(f"{n}. **[{item['summary'].split(':')[0]}] Title** - {item['file']}:{item['line']}" for n,item in enumerate(selected,1)) or 'None.'
            text+='\n'
        text+='### ✅ Good Practices\n- Positive observation, no remediation.\n### Verdict\nApprove with Suggestions\n'
        return text+self.report(dict(findings=items if structured is None else structured,tests_run=[dict(command='pytest',outcome='not_run',details='Read test source only')]))

    def test_mixed_review_and_positive_observations_match_exact_ids(self):
        items=[dict(severity=severity,summary=f'F00{n}: Title',file='fixture.py',line=n) for n,severity in enumerate(('critical','warning','info'),1)]
        findings,checks,limitations=extract(self.review(items))
        self.assertEqual([item['severity'] for item in findings],['critical','warning','info'])
        self.assertEqual([item['summary'].split(':')[0] for item in findings],['F001','F002','F003'])
        self.assertEqual(checks[0]['outcome'],'not_run');self.assertEqual(limitations,[])

    def test_true_no_findings_differs_from_rejected_or_missing_structure(self):
        self.assertEqual(extract(self.review([]))[2],[])
        item=dict(severity='warning',summary='F001: Reachable fixture',file='fixture.py',line=3)
        for invalid in ('high','medium','low','unknown'):
            findings,checks,limits=extract(self.review([item],[dict(item,severity=invalid)]))
            self.assertEqual(findings,[]);self.assertTrue(any('Invalid findings entry' in line for line in limits))
            self.assertTrue(any('correspondence incomplete' in line for line in limits))
        for value in ({},{'findings':[]},{'findings':[],'tests_run':None}):
            self.assertTrue(extract('## Code Review: missing required arrays\n'+self.report(value))[2])

    def test_prefaced_and_missing_headers_cannot_disable_correspondence(self):
        item=dict(severity='info',summary='F001: Suggestion',file='fixture.py',line=1)
        valid=self.review([item])
        self.assertEqual(extract('A preface.\n'+valid)[2],[])
        mismatched=self.review([item],[dict(item,summary='F002: Different')])
        for text in ('A preface.\n'+mismatched,valid.replace('## Code Review:','## code review:'),valid.replace('## Code Review:','Plain heading:')):
            self.assertTrue(any('correspondence incomplete' in line for line in extract(text)[2]))

    def test_markdown_fix_blocks_do_not_fabricate_readable_items(self):
        item=dict(severity='critical',summary='F001: Patch markdown',file='fixture.md',line=1)
        valid=self.review([item])
        patch='\n   - Risk: input 1,2 follows subtraction and returns -1 instead of 3.\n   - Fix:\n```markdown\n### Warnings (Should Fix)\n1. **[F002] Example only** - example.md:9\n```\n'
        valid=valid.replace('### 🟡 Warnings',patch+'### 🟡 Warnings')
        self.assertEqual(extract(valid)[2],[])
        # A fenced item cannot supply a missing real readable ID.
        forged=valid.replace('1. **[F001] Title** - fixture.md:1','None.').replace('[F002] Example only** - example.md:9','[F001] Example only** - fixture.md:1')
        self.assertTrue(any('correspondence incomplete' in line for line in extract(forged)[2]))

    def test_correspondence_includes_locations_and_retains_overflow(self):
        item=dict(severity='info',summary='F001: Suggestion',file='fixture.py',line=1)
        for structured in ([dict(item,file='other.py')],[dict(item,line=2)],[dict(severity='info',summary=item['summary'])]):
            self.assertTrue(any('correspondence incomplete' in line for line in extract(self.review([item],structured))[2]))
        items=[dict(item,summary=f'F{i:03}: Suggestion') for i in range(1,22)]
        findings,checks,limits=extract(self.review(items))
        self.assertEqual(findings,[]);self.assertIn('Invalid findings list; provider final text retained.',limits)

    def test_generic_optional_reports_remain_optional(self):
        from nativepeers.worker_report import report_incomplete
        for value in ({},{'findings':[dict(severity='info',summary='ordinary implementation observation')]},{'tests_run':[]}):
            findings,checks,limits=extract(self.report(value));self.assertEqual(limits,[])
        limits=extract('Ordinary implementation answer.')[2]
        self.assertFalse(report_incomplete('Ordinary implementation answer.',limits))
        self.assertTrue(report_incomplete('## Code Review: missing appendix',limits))

    def test_correspondence_rejects_missing_extra_duplicate_and_recategorized_ids(self):
        item=dict(severity='info',summary='F001: Useful suggestion',file='fixture.py',line=3)
        for structured in ([],[dict(item,summary='F002: Other')],[item,item],[dict(item,severity='warning')]):
            with self.subTest(structured=structured):
                self.assertTrue(any('correspondence incomplete' in line for line in extract(self.review([item],structured))[2]))

    def test_closed_single_appendix_and_check_outcomes(self):
        raw=self.report(dict(findings=[],tests_run=[dict(command='pytest',outcome=outcome) for outcome in ('passed','failed','not_run','success')]))
        findings,checks,limits=extract(raw)
        self.assertEqual([item['outcome'] for item in checks],['passed','failed','not_run'])
        self.assertIn('Invalid tests_run entry; provider final text retained.',limits)
        for invalid in (raw.rsplit('```',1)[0],raw+raw):
            self.assertIn('Malformed structured worker report; provider final text retained.',extract(invalid)[2])


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
