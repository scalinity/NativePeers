import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nativepeers.caller import native_binaries
from nativepeers.cli import dispatch, parser
from nativepeers.errors import PeerError
from nativepeers.journal import Journal


class GrokDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_receipt_refuses_identity_but_keeps_passive_diagnostics(self):
        for raw in ('not JSON','[]','{"providers":[]}'):
            with self.subTest(raw=raw),tempfile.TemporaryDirectory() as directory:
                home=Path(directory);journal=Journal(home/'state')
                self.addCleanup(journal.close)
                receipt=journal.root/'compat/runtime.json';receipt.parent.mkdir(exist_ok=True);receipt.write_text(raw)
                with patch('pathlib.Path.home',return_value=home),patch.dict(os.environ,{},clear=True):
                    with self.assertRaises(PeerError) as error:native_binaries(journal)
                    self.assertEqual(error.exception.code,'CAPABILITY_MISMATCH')
                    self.assertEqual(await dispatch(parser().parse_args(['list','--json']),journal),[])
                    report=await dispatch(parser().parse_args(['doctor','--json']),journal)
                self.assertEqual(len(report['compatibility']),4)
                self.assertTrue(all(row['state']=='CAPABILITY_MISMATCH' for row in report['compatibility']))
                self.assertEqual(receipt.read_text(),raw)
                json.dumps(report)

    async def test_invalid_retained_paths_cannot_silently_drop_a_native_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory);journal=Journal(home/'state');self.addCleanup(journal.close)
            receipt=journal.root/'compat/runtime.json';receipt.parent.mkdir(exist_ok=True)
            receipt.write_text(json.dumps({'providers':{},'retained':{'grok':[]}}))
            with self.assertRaises(PeerError) as error:native_binaries(journal)
            self.assertEqual(error.exception.code,'CAPABILITY_MISMATCH')
