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

    def test_retained_runtime_is_scoped_to_preexisting_verified_leases_and_revocable(self):
        from nativepeers.compatibility import revoke
        from nativepeers.errors import PeerError
        from nativepeers.files import canonical
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();j=Journal(root/'state');self.addCleanup(j.close)
            old=root/'codex-v1';old.write_text('verified old package');new=root/'codex-v2';new.write_text('verified new package')
            claude=root/'claude';claude.write_text('unchanged')
            approved=dict(fingerprint(old),version='old',scope='focused native seam approval')
            atomic_json(j.root/'compat/runtime.json',{'providers':{'codex':approved}})
            j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',('group','parent','{}','COMPLETED','{}'))
            def worker(name,pinned):
                lease={'executable':str(old),'verified_runtime':pinned} if pinned else {'executable':str(old)}
                j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(name,'group',None,'codex',name,name,'model','model',None,str(root),None,'RELEASED_RESUMABLE',1,canonical(lease).decode()))
            worker('owned-old',fingerprint(old));worker('unverified-old',None)
            source=root/'verified.json';source.write_text(json.dumps({'providers':{p:{'executable':str(f),'executable_sha256':fingerprint(f)['sha256'],'version':'new'} for p,f in [('claude',claude),('codex',new)]}}))
            record_verified(j,source)
            self.assertEqual(verify(j,'codex',new)['path'],str(new))
            self.assertEqual(verify(j,'codex',old,worker_id='owned-old')['path'],str(old))
            worker('later-copy',fingerprint(old))
            for worker_id in (None,'unverified-old','later-copy'):
                with self.subTest(worker=worker_id),self.assertRaises(PeerError):verify(j,'codex',old,worker_id=worker_id)
            # A lease cannot silently migrate to the newly approved package.
            with self.assertRaises(PeerError):verify(j,'codex',new,worker_id='owned-old')
            result=revoke(j,'codex',approved['sha256']);self.assertTrue(result['current_approval_unchanged'])
            record_verified(j,source)  # no-op refresh cannot undo revocation
            with self.assertRaises(PeerError):verify(j,'codex',old,worker_id='owned-old')
            self.assertEqual(verify(j,'codex',new)['path'],str(new))
            old.write_text('tampered old bytes')
            with self.assertRaises(PeerError):verify(j,'codex',old,worker_id='owned-old')

    def test_public_revocation_refuses_worker_authority(self):
        import asyncio
        from unittest.mock import patch
        from nativepeers.caller import Caller
        from nativepeers.cli import parser,dispatch
        from nativepeers.errors import PeerError
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);j=Journal(root/'state');self.addCleanup(j.close)
            digest='a'*64
            atomic_json(j.root/'compat/runtime.json',{'providers':{},'retained':{'codex':{digest:{'fingerprint':{},'workers':['old-worker'],'revoked':False}}}})
            args=parser().parse_args(['runtime-revoke','--provider','codex','--sha256',digest])
            with patch('nativepeers.cli.bind_caller',return_value=Caller('peer','worker',grants={'worker_id':'worker'})),self.assertRaises(PeerError):asyncio.run(dispatch(args,j))
            with patch('nativepeers.cli.bind_caller',return_value=Caller('external-local','local')):
                self.assertTrue(asyncio.run(dispatch(args,j))['revoked'])
