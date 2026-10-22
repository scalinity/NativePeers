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
