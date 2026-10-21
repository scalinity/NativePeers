import io
import json
import unittest
from unittest.mock import AsyncMock,Mock,patch

from nativepeers.cli import main


class CliTests(unittest.TestCase):
    def run_outcome(self,state):
        value={'message_id':'message-fixture','client_request_id':'request-fixture','state':state,'attempt':{'state':state,'error_json':None},'receipts':[]}
        output=io.StringIO()
        with patch('sys.argv',['npeer','send','--to','fixture','--message','fixture']),patch('nativepeers.cli.Journal',return_value=Mock()),patch('nativepeers.cli.dispatch',new=AsyncMock(return_value=value)),patch('sys.stdout',output):
            with self.assertRaises(SystemExit) as exit:main()
        return exit.exception.code,json.loads(output.getvalue())

    def test_definite_send_failures_use_nonzero_exit_and_retain_request_identity(self):
        for state,code in [('TARGET_GONE',3),('REJECTED',4),('EXPIRED_BEFORE_SUBMISSION',8),('CAPABILITY_MISMATCH',5),('BUDGET_EXHAUSTED',8),('APPROVAL_BLOCKED',4),('UNSUPPORTED_TRANSPORT',5)]:
            with self.subTest(state=state):
                exit,response=self.run_outcome(state)
                self.assertEqual(exit,code);self.assertFalse(response['ok']);self.assertFalse(response['error']['may_have_received'])
                self.assertEqual(response['data']['message_id'],'message-fixture');self.assertEqual(response['data']['client_request_id'],'request-fixture')
                self.assertEqual(response['error']['code'],'PROVIDER_POLICY_REFUSED' if state=='REJECTED' else state)

    def test_accepted_held_and_uncertain_receipts_keep_their_distinct_states(self):
        for state,code in [('ACCEPTED_QUEUED',0),('INGESTED',0),('HELD',0),('DELIVERY_UNKNOWN',6),('SUBMITTING',6),('SUBMITTED_UNCONFIRMED',6),('OUTBOX_COMMITTED',6)]:
            with self.subTest(state=state):
                exit,response=self.run_outcome(state)
                self.assertEqual(exit,code);self.assertEqual(response['data']['state'],state);self.assertTrue(response['ok'])
