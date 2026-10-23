"""Status and inbox share the native-recipient withholding boundary."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from nativepeers.artifacts import Artifacts
from nativepeers.caller import Caller
from nativepeers.cli import dispatch,parser
from nativepeers.core import Core
from nativepeers.errors import PeerError
from nativepeers.files import atomic_json
from nativepeers.journal import Journal
from nativepeers.mcp_server import Facade
from nativepeers.models import new_id
from nativepeers.process import identity


class MessageVisibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_recipient_content_requires_ingestion_through_cli_and_mcp(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);j=Journal(root/'state');self.addCleanup(j.close);core=Core(j)
            peers=[core.registry.register('codex',root/new_id(),new_id(),title,identity(os.getpid()).json(),str(root),
                {'kind':'fixture'},owner_kind='interactive-standalone' if title=='recipient' else 'test')
                for title in ('sender','recipient','unrelated')]
            j.db.execute("UPDATE peer_instances SET status='live'")
            sender,recipient,other=[Caller('peer',p['handle'],p['incarnation_id']) for p in peers]
            context=Artifacts(j).put(b'WITHHELD CONTEXT',sender.key,[sender.key,recipient.key])
            binding=root/'binding';atomic_json(binding,{'state':'BOUND','grants':{}});facade=Facade(j,binding)
            for stage in ('HELD','REJECTED','SUBMITTED_UNCONFIRMED'):
                with self.subTest(stage=stage):
                    async def deliver(*args):return dict(stage=stage,evidence_kind='fixture')
                    core.router.adapters['fixture']=deliver
                    out=await core.router.send(sender,recipient.key,'WITHHELD BODY',artifact_refs=[context])
                    mid=out['message_id']
                    self.assertFalse(core.router.inbox(recipient)[0]['body_available'])
                    for result in (await core.status(recipient,mid),await core.tool(recipient,'task_status',{'handle':mid})):
                        self.assertNotIn(context,json.dumps(result));self.assertNotIn('WITHHELD',json.dumps(result));self.assertFalse(result['message']['body_available'])
                    with patch('nativepeers.mcp_server.bind_caller',return_value=recipient):
                        result=await facade.call(None,SimpleNamespace(name='task_status',arguments={'handle':mid}))
                    self.assertNotIn('WITHHELD',result.content[0].text)
                    with patch('nativepeers.cli.bind_caller',return_value=recipient):
                        result=await dispatch(parser().parse_args(['status',mid,'--json']),j)
                    self.assertNotIn('WITHHELD',json.dumps(result))
                    self.assertIn('WITHHELD BODY',json.dumps(await core.status(sender,mid)))
                    core.router.ack(recipient,mid)
                    self.assertFalse(core.router.inbox(recipient)[0]['body_available'])
                    self.assertNotIn('WITHHELD',json.dumps(await core.status(recipient,mid)))
                    await core.router.send(recipient,sender.key,'reply claim without ingestion',in_reply_to=mid)
                    self.assertNotIn('WITHHELD',json.dumps(await core.status(recipient,mid)))
                    with self.assertRaises(PeerError):await core.status(other,mid)
                    core.router.record(mid,out['attempt']['attempt_id'],dict(stage='INGESTED',evidence_kind='exact-native-input'),peers[1])
                    self.assertIn('WITHHELD BODY',json.dumps(await core.status(recipient,mid)))
                    self.assertIn(context,json.dumps(core.router.inbox(recipient)))
