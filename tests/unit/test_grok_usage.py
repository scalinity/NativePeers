import json
import unittest
from unittest.mock import patch

from nativepeers.errors import PeerError
from nativepeers.usage.collectors import GrokCollector
from nativepeers.usage.grok_native import parse, session_usage


NATIVE='97432fc5-c7a9-4039-85a8-a2c4abef549b'


def report():
    # Shape and first-turn counts measured with Grok 1.0.70 `usage UUID`.
    row=dict(turnNumber=1,inputTokens=23214,outputTokens=40,cachedReadTokens=3072,
        cacheCreationTokens=0,reasoningTokens=34,totalTokens=23254,
        primaryModelId='grok-4.7-build')
    return dict(sessionId=NATIVE,session=dict(row,totalTokens=999999),turns=[row])


class GrokUsageTests(unittest.TestCase):
    def test_persisted_turn_keeps_native_totals_and_engine_attribution(self):
        usage=parse(json.dumps(report()),NATIVE)
        self.assertEqual(usage['total_tokens'],23254)
        self.assertEqual(usage['input_tokens'],23214)
        self.assertEqual(usage['cache_read_input_tokens'],3072)
        self.assertEqual(usage['engine_model'],'grok-4.7-build')
        self.assertEqual(usage['counter_scope'],'native-turn')

    def test_resume_reports_latest_turn_without_cumulative_double_counting(self):
        value=report();second=dict(value['turns'][0],turnNumber=2,totalTokens=107,inputTokens=100,outputTokens=7)
        value['turns'].append(second)
        self.assertEqual(parse(value,NATIVE)['total_tokens'],107)
        self.assertEqual(parse(value,NATIVE,turn=1)['total_tokens'],23254)

    def test_wrong_uuid_invalid_counts_and_duplicate_turns_are_refused(self):
        cases=[]
        wrong=report();wrong['sessionId']='another-session';cases.append(wrong)
        duplicate=report();duplicate['turns']*=2;cases.append(duplicate)
        for bad in (-1,True,1.5,None):
            value=report();value['turns'][0]['totalTokens']=bad;cases.append(value)
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(PeerError) as raised:parse(value,NATIVE)
                self.assertEqual(raised.exception.code,'SOURCE_SCHEMA_MISMATCH')

    def test_missing_turn_is_not_replaced_with_session_totals(self):
        for value in ({'sessionId':NATIVE,'session':{'totalTokens':100}},dict(report(),turns=[])):
            with self.assertRaises(PeerError):parse(value,NATIVE)


class GrokUsageAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_account_quota_stays_unknown_without_running_a_native_command(self):
        with patch('nativepeers.usage.native_command.run_native',side_effect=AssertionError('unexpected command')):
            value=await GrokCollector().collect(None,None)
        self.assertEqual(value['pools'],[])
        self.assertEqual(value['collection']['outcome'],'UNAVAILABLE')
        self.assertEqual(value['coverage'],'unknown')

    async def test_only_owned_session_can_launch_usage_command(self):
        class Journal:
            def one(self,*args):return None
        with self.assertRaises(PeerError) as raised:
            await session_usage(Journal(),'/home/grok',NATIVE,'/bin/grok','/fixture')
        self.assertEqual(raised.exception.code,'OWNERSHIP_UNKNOWN')
