import json
import unittest
from nativepeers.worker_report import extract


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
