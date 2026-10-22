import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from nativepeers.caller import Caller
from nativepeers.cli import main
from nativepeers.core import Core
from nativepeers.errors import PeerError
from nativepeers.journal import Journal
from nativepeers.mcp_server import Facade
from nativepeers.process import identity


class DeliveryEnvelopeTests(unittest.TestCase):
    def test_cli_and_mcp_preserve_failed_send_and_reply_outcomes(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal=Journal(Path(tmp));self.addCleanup(journal.close)
            core=Core(journal);registry=core.registry
            a=registry.register('codex',Path(tmp)/'a','00000000-0000-4000-8000-000000000001','a',identity(os.getpid()).json(),tmp,{'kind':'fake'},owner_kind='test')
            b=registry.register('codex',Path(tmp)/'b','00000000-0000-4000-8000-000000000002','b',identity(os.getpid()).json(),tmp,{'kind':'fake'},owner_kind='test')
            journal.db.execute("UPDATE peer_instances SET status='live'")
            ca=Caller('peer',a['handle'],a['incarnation_id']);cb=Caller('peer',b['handle'],b['incarnation_id'])
            async def queued(peer,message):return {'stage':'ACCEPTED_QUEUED','evidence_kind':'fake'}
            core.router.adapters['fake']=queued
            first=asyncio.run(core.router.send(cb,a['handle'],'request'))
            cases=[('REJECTED',False,4),('TARGET_GONE',False,3),('PERMISSION_REQUIRED',False,5),
                   ('ACCEPTED_QUEUED',True,0),('HELD',True,0),('DELIVERY_UNKNOWN',True,6)]
            for operation in ('send','reply'):
                for state,ok,exit_code in cases:
                    with self.subTest(operation=operation,state=state):
                        async def deliver(peer,message):
                            if state in ('TARGET_GONE','PERMISSION_REQUIRED'):
                                raise PeerError(state,'fake definite failure')
                            return {'stage':state,'evidence_kind':'fake'}
                        core.router.adapters['fake']=deliver
                        args={'text':'test','client_request_id':operation+state}
                        args.update(target=b['handle']) if operation=='send' else args.update(message_id=first['message_id'])
                        facade=Facade(journal,None)
                        with patch.object(facade,'binding_state',return_value={'state':'BOUND','grants':{}}),patch('nativepeers.mcp_server.bind_caller',return_value=ca),patch('nativepeers.core.Core',return_value=core):
                            result=asyncio.run(facade.call(None,SimpleNamespace(name='peer_'+operation,arguments=args)))
                        envelope=json.loads(result.content[0].text)
                        self.assertEqual(envelope['ok'],ok);self.assertEqual(result.is_error,not ok)
                        self.assertEqual(envelope['data']['state'],state)
                        self.assertEqual(envelope['data']['client_request_id'],args['client_request_id'])
                        self.assertTrue(envelope['data']['message_id']);self.assertTrue(envelope['data']['exchange_id']);self.assertTrue(envelope['data']['attempt'])
                        value=envelope['data']
                        async def dispatch(*unused):return value
                        argv=['npeer',operation]+(['--to','b'] if operation=='send' else [first['message_id']])+['--message','test','--json']
                        output=io.StringIO()
                        with patch('sys.argv',argv),patch('nativepeers.cli.Journal',return_value=journal),patch.object(journal,'close'),patch('nativepeers.cli.dispatch',side_effect=dispatch),contextlib.redirect_stdout(output),self.assertRaises(SystemExit) as raised:
                            main()
                        cli=json.loads(output.getvalue())
                        self.assertEqual(raised.exception.code,exit_code)
                        self.assertEqual(cli['ok'],envelope['ok']);self.assertEqual(cli['error'],envelope['error']);self.assertEqual(cli['data'],envelope['data'])
