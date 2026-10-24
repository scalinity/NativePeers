"""Renewal refuses stale native evidence and preserves other operation approvals."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nativepeers.compatibility import fingerprint
from nativepeers.errors import PeerError
from nativepeers.files import atomic_json
from nativepeers.journal import Journal

spec=importlib.util.spec_from_file_location('claude_runtime_probe',Path(__file__).resolve().parents[1]/'probes/claude_runtime_verify.py')
probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)


class ClaudeRuntimeRenewalTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve();self.run=self.root/'run';self.run.mkdir(mode=0o700)
        self.home=self.root/'home';self.binary=self.home/'.local/bin/claude'
        self.binary.parent.mkdir(parents=True);self.binary.write_text('qualified fixture binary')
        (self.home/'.claude').mkdir();(self.home/'.claude/settings.json').write_text('{}')
        self.runtime=fingerprint(self.binary);self.state=self.root/'daily'
        journal=Journal(self.state);journal.close()
        self.old=dict(path='/old/claude',sha256='a'*64,size=1)
        self.codex=dict(path='/keep/codex',sha256='b'*64,size=2)
        atomic_json(self.state/'compat/runtime.json',{'providers':{'claude':self.old,'codex':self.codex}})
        atomic_json(self.run/'inspect.json',dict(runtime=self.runtime,source=probe.sources(),daily_state=str(self.state),
            expected_approval=self.old,version='fixture'))
        atomic_json(self.run/'qualification.json',dict(status='PASS',runtime=self.runtime,source=probe.sources()))
        self.enterContext(patch('pathlib.Path.home',return_value=self.home))

    def test_exact_guarded_transition_preserves_unrelated_runtime_and_quota_receipts(self):
        usage=self.state/'compat/usage-sources.json';usage.write_text('unrelated quota fixture')
        from nativepeers.install import Installer
        original=Installer.__init__
        def isolated(instance,journal):original(instance,journal,native=False)
        with patch.object(Installer,'__init__',isolated):result=probe.approve(self.run)
        self.assertTrue(result['approved'])
        document=json.loads((self.state/'compat/runtime.json').read_text())
        self.assertEqual(document['providers']['codex'],self.codex)
        self.assertEqual(document['providers']['claude']['sha256'],self.runtime['sha256'])
        self.assertEqual(usage.read_text(),'unrelated quota fixture')

    def test_auto_update_race_cannot_publish_an_untested_fingerprint(self):
        before=(self.state/'compat/runtime.json').read_bytes()
        self.binary.write_text('new unknown automatic update')
        from nativepeers.install import Installer
        original=Installer.__init__
        def isolated(instance,journal):original(instance,journal,native=False)
        with patch.object(Installer,'__init__',isolated),self.assertRaises(PeerError) as error:probe.approve(self.run)
        self.assertEqual(error.exception.code,'CAPABILITY_MISMATCH')
        self.assertEqual((self.state/'compat/runtime.json').read_bytes(),before)

    def test_concurrent_approval_and_changed_sources_are_refused(self):
        document=json.loads((self.state/'compat/runtime.json').read_text())
        document['providers']['claude']=dict(self.old,sha256='c'*64)
        atomic_json(self.state/'compat/runtime.json',document)
        before=(self.state/'compat/runtime.json').read_bytes()
        from nativepeers.install import Installer
        original=Installer.__init__
        def isolated(instance,journal):original(instance,journal,native=False)
        with patch.object(Installer,'__init__',isolated),self.assertRaises(PeerError):probe.approve(self.run)
        self.assertEqual((self.state/'compat/runtime.json').read_bytes(),before)

    def test_approval_serializes_with_installed_renamed_manager(self):
        manager=self.home/'renamed-project/manager.py';manager.parent.mkdir()
        marker=self.root/'manager-lock-used'
        manager.write_text('from contextlib import contextmanager\nfrom pathlib import Path\n@contextmanager\ndef update_lock():\n    Path('+repr(str(marker))+').write_text("locked")\n    yield\n')
        launcher=self.home/'.local/bin/codex-mcp';launcher.symlink_to(manager)
        result=probe.approve(self.run)
        self.assertTrue(result['approved']);self.assertEqual(marker.read_text(),'locked')
        before=(self.state/'compat/runtime.json').read_bytes()
        with patch.object(probe,'sources',return_value={}),self.assertRaises(PeerError):probe.approve(self.run)
        self.assertEqual((self.state/'compat/runtime.json').read_bytes(),before)

    def test_stale_source_guard_fails_before_any_lock_or_receipt_comparison(self):
        before=(self.state/'compat/runtime.json').read_bytes()
        with patch.object(probe,'sources',return_value={}),patch('nativepeers.install.Installer.lock') as lock,self.assertRaises(PeerError):
            probe.approve(self.run)
        lock.assert_not_called();self.assertEqual((self.state/'compat/runtime.json').read_bytes(),before)

    def test_parent_permission_rules_are_exact_and_prepared_inspection_cannot_redirect_target(self):
        probe.prepare(self.run,self.runtime['sha256'])
        settings=json.loads((self.run/'settings.json').read_text())
        rules=settings['permissions']['allow']
        self.assertEqual(len(rules),2);self.assertFalse(any('*' in rule for rule in rules))
        self.assertIn(' parent --run '+str(self.run)+')',rules[0])
        self.assertIn(' receipt --run '+str(self.run)+')',rules[1])
        before=(self.run/'inspect.json').read_bytes()
        with self.assertRaises(PeerError):probe.inspect(self.run)
        self.assertEqual((self.run/'inspect.json').read_bytes(),before)
        value=json.loads(before);value['daily_state']=str(self.run/'state');atomic_json(self.run/'inspect.json',value)
        with self.assertRaises(PeerError) as error:probe.approve(self.run)
        self.assertEqual(error.exception.code,'APPROVAL_BLOCKED')

    def test_missing_or_unsupported_installed_manager_is_a_typed_refusal(self):
        before=(self.state/'compat/runtime.json').read_bytes()
        for scenario in ('missing','regular','unsupported-link'):
            launcher=self.home/'.local/bin/codex-mcp'
            if launcher.exists() or launcher.is_symlink():launcher.unlink()
            if scenario=='regular':launcher.write_text('unmanaged')
            if scenario=='unsupported-link':launcher.symlink_to(self.binary)
            with self.subTest(scenario=scenario),self.assertRaises(PeerError) as error:probe.approve(self.run)
            self.assertEqual(error.exception.code,'CAPABILITY_MISMATCH')
            self.assertEqual((self.state/'compat/runtime.json').read_bytes(),before)
