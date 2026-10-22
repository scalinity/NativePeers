import json
from pathlib import Path
import tempfile
import unittest

from nativepeers.compatibility import fingerprint,record_verified,verify
from nativepeers.files import atomic_json
from nativepeers.journal import Journal


class CompatibilityReceiptTests(unittest.TestCase):
    def test_refreshing_existing_providers_preserves_the_installed_third_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);journal=Journal(root/'state');providers={}
            for name in ('claude','codex'):
                path=root/name;path.write_text(name);value=fingerprint(path)
                providers[name]={'executable':str(path),'executable_sha256':value['sha256'],'version':'fixture'}
            third={'path':'official-agy','sha256':'retained-native-fingerprint','scope':'recorded native tests'}
            atomic_json(journal.root/'compat/runtime.json',{'providers':{'antigravity':third}})
            source=root/'capabilities.json';source.write_text(json.dumps({'providers':providers}))
            record_verified(journal,source)
            self.assertEqual(json.loads((journal.root/'compat/runtime.json').read_text())['providers']['antigravity'],third)
            with self.assertRaises(Exception) as error:verify(journal,'missing',root/'claude')
            self.assertEqual(error.exception.code,'CAPABILITY_MISMATCH')
            journal.close()

    def test_diagnosis_explains_changed_missing_and_matching_executables_passively(self):
        from nativepeers.compatibility import diagnose
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);journal=Journal(root/'state');self.addCleanup(journal.close)
            executable=root/'native';executable.write_text('original')
            expected=dict(fingerprint(executable),version='fixture',scope='queue and worker fixture')
            atomic_json(journal.root/'compat/runtime.json',{'providers':{'codex':expected}})
            before=(journal.root/'compat/runtime.json').read_bytes()
            self.assertEqual(diagnose(journal,{'codex':executable})[0]['state'],'PASS')
            executable.write_text('changed binary')
            with patch('subprocess.Popen',side_effect=AssertionError('must remain passive')):
                result=diagnose(journal,{'codex':executable,'claude':root/'missing'})
            self.assertEqual(result[0]['state'],'CAPABILITY_MISMATCH')
            self.assertEqual(result[0]['expected'],expected)
            self.assertNotEqual(result[0]['current']['sha256'],expected['sha256'])
            self.assertIn('native queue',result[0]['affected_operations'])
            self.assertEqual(result[0]['verification_scope'],expected['scope'])
            self.assertEqual(result[1]['error'],'FileNotFoundError')
            self.assertEqual((journal.root/'compat/runtime.json').read_bytes(),before)
            self.assertEqual(journal.all('SELECT * FROM receipts'),[])
