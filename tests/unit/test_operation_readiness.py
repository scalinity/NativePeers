import hashlib
import os
import time
from unittest.mock import patch

import pytest

from nativepeers.caller import binding_readiness, read_binding
from nativepeers.compatibility import fingerprint
from nativepeers.errors import PeerError
from nativepeers.files import atomic_json
from nativepeers.journal import Journal
from nativepeers.process import identity
from nativepeers.readiness import operation_readiness
from nativepeers.usage.native_command import BROWSER_PROFILE


def test_usage_approval_does_not_approve_workers_in_readiness_matrix(tmp_path):
    j=Journal(tmp_path/'state');binary=tmp_path/'.local/bin/claude';binary.parent.mkdir(parents=True);binary.write_text('current native')
    runtime=fingerprint(binary)
    atomic_json(j.root/'compat/runtime.json',{'providers':{'claude':dict(runtime,sha256='old-worker-approval')}})
    atomic_json(j.root/'compat/usage-sources.json',{'claude':{'provider':'claude','runtime':runtime,'qualified':True,
        'browser_guard_sha256':hashlib.sha256(BROWSER_PROFILE.encode()).hexdigest(),'pty_authorized':True,
        'route':'native-pty-usage','managed_startup':{}}})
    checks={'providers':{p:{'checks':[{'state':'PRESENT'}]} for p in ('claude','codex','antigravity')}}
    try:
        with patch('pathlib.Path.home',return_value=tmp_path),patch('nativepeers.readiness.readiness',return_value=checks),patch('nativepeers.usage.contracts.managed_claude_startup',return_value={}),patch.dict(os.environ,{},clear=True):
            before=j.db.total_changes;value=operation_readiness(j)[0]
        assert value['provider']=='claude'
        assert value['operations']['quota']['state']=='APPROVED'
        assert value['operations']['worker_turns']['state']=='CAPABILITY_MISMATCH'
        assert value['configuration_layout']=='implicit ~/.claude.json'
        assert value['operations']['mcp']['state']=='UNKNOWN' and j.db.total_changes==before
    finally:j.close()


def test_binding_diagnosis_distinguishes_reserved_expired_and_generation(tmp_path):
    j=Journal(tmp_path/'state');binding=j.root/'bindings/fixture';owner=identity(os.getpid()).json()
    value={'state':'RESERVED_UNBOUND','owner':owner,'expires_at':time.time()+10,'provider':'claude'}
    try:
        atomic_json(binding,value)
        with pytest.raises(PeerError) as error:read_binding(j,binding)
        assert error.value.code=='CALLER_NOT_READY' and error.value.details['reason']=='NATIVE_IDENTITY_RESERVED'
        assert 'normal' in error.value.details['next_binding_event']
        assert binding_readiness(j,dict(value,state='BOUND',expires_at=0))['reason']=='LEASE_EXPIRED'
        assert binding_readiness(j,dict(value,state='BOUND',owner=dict(owner,start='wrong')))['reason']=='OWNER_GONE'
    finally:j.close()
