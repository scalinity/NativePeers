import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from nativepeers.artifacts import Artifacts
from nativepeers.caller import Caller,bind_caller
from nativepeers.errors import PeerError
from nativepeers.files import atomic_json
from nativepeers.journal import Journal
from nativepeers.launch import launch_environment
from nativepeers.process import identity
from nativepeers.registry import Registry
from nativepeers.provider_errors import classify


class ArtifactsAndAuthority(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.j=Journal(Path(self.temp.name));self.addCleanup(self.j.close)

    def test_distinct_large_result_channel_and_reader_grants(self):
        artifacts=Artifacts(self.j);handle=artifacts.put(b'x'*300000,'owner',['reader'])
        self.assertEqual(artifacts.read(handle,'reader',limit=262144)['bytes_read'],262144)
        self.assertEqual(artifacts.read(handle,'reader',offset=262144)['bytes_read'],37856)
        for who in ['stranger','../../etc/passwd']:
            with self.assertRaises(PeerError):artifacts.read(handle,who)
        with self.assertRaises(PeerError):artifacts.read(handle,'owner',limit=262145)
        with self.assertRaises(PeerError):artifacts.put(b'x'*10485761,'owner')

    def test_tamper_and_symlink_are_rejected(self):
        artifacts=Artifacts(self.j);handle=artifacts.put(b'original','owner');path=self.j.root/'artifacts'/handle
        path.write_bytes(b'changed')
        with self.assertRaises(PeerError):artifacts.read(handle,'owner')
        path.unlink();path.symlink_to('/etc/hosts')
        with self.assertRaises(OSError):artifacts.read(handle,'owner')

    def test_unbound_forged_metadata_never_becomes_human(self):
        with patch.dict(os.environ,{'CODEX_THREAD_ID':'00000000-0000-4000-8000-000000000001'},clear=True):
            with self.assertRaises(PeerError) as error:bind_caller(self.j,Registry(self.j))
            self.assertEqual(error.exception.code,'CALLER_UNBOUND')
        with patch.dict(os.environ,{},clear=True):self.assertEqual(bind_caller(self.j,Registry(self.j)).kind,'external-local')

    def test_private_bootstrap_is_inactive_and_expiry_is_enforced(self):
        binding=self.j.root/'bindings/test';atomic_json(binding,{'state':'RESERVED_UNBOUND'})
        with self.assertRaises(PeerError) as error:bind_caller(self.j,Registry(self.j),fixed_binding=binding)
        self.assertEqual(error.exception.code,'CALLER_NOT_READY')
        atomic_json(binding,{'state':'BOUND','owner':identity(os.getpid()).json(),'expires_at':time.time()-1})
        with self.assertRaises(PeerError):bind_caller(self.j,Registry(self.j),fixed_binding=binding)

    def test_launch_context_drops_parent_bindings_and_keeps_native_home(self):
        with patch.dict(os.environ,{'NPEER_BINDING':'private','CLAUDE_ENV_FILE':'parent-file','CLAUDE_CODE_MESSAGING_TOKEN':'private','CODEX_THREAD_ID':'parent','CODEX_HOME':'/native-home'},clear=True):
            environment=launch_environment('codex');self.assertEqual(environment,{'CODEX_HOME':'/native-home'})
        with patch.dict(os.environ,{'CLAUDECODE':'1'},clear=True):
            with self.assertRaisesRegex(RuntimeError,'nested Claude'):launch_environment('claude')

    def test_provider_failure_taxonomy(self):
        examples={'AUTH_REQUIRED':'authentication failed 401','MODEL_UNAVAILABLE':'model_not_found','EFFORT_UNSUPPORTED':'unsupported effort',
                  'QUOTA_EXHAUSTED':'usage limit reached','RATE_LIMITED':'429 rate limit','APPROVAL_BLOCKED':'permission denied','BUDGET_EXHAUSTED':'error_max_turns'}
        for expected,error in examples.items():self.assertEqual(classify({'error':error}),expected)
