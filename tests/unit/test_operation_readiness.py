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


def test_whoami_uses_the_live_claude_binding_after_continue(tmp_path):
    import asyncio
    from pathlib import Path
    from nativepeers.cli import dispatch,parser
    from nativepeers.process import identity
    from nativepeers.registry import Registry
    j=Journal(tmp_path/'state');owner=identity(os.getpid());home=tmp_path/'claude-home';home.mkdir()
    peer=Registry(j).register('claude',home,'6f1c2a40-7b0e-4c1a-9d3e-2a8b5c6d7e8f','continued',owner.json(),tmp_path,{'kind':'fake'})
    live=j.root/'bindings'/'live';stale=j.root/'bindings'/'stale'
    body=dict(state='BOUND',kind='peer',peer_key=peer['handle'],incarnation_id=peer['incarnation_id'],expires_at=time.time()+3600)
    atomic_json(live,dict(body,owner=owner.json()))
    atomic_json(stale,dict(body,owner=dict(owner.json(),pid=2**30,start='0:0')))
    binaries={Path(owner.executable):'claude'}
    environment={'NPEER_BINDING':str(stale),'NPEER_BOOTSTRAP':'','CODEX_THREAD_ID':''}
    try:
        with patch('nativepeers.caller.ancestry',return_value=[owner]),patch('nativepeers.caller.native_binaries',return_value=binaries),patch.dict(os.environ,environment):
            result=asyncio.run(dispatch(parser().parse_args(['whoami','--json']),j))
        assert result['kind']=='peer' and result['key']==peer['handle']
        assert result['notification_policy']=={'state':'OPEN','configured':False,'message_cutoff':0,'route_cutoff':0,'closed_at':None,'opened_at':None,'written_by_incarnation':None}
        extra=j.root/'bindings'/'also-live';atomic_json(extra,dict(body,owner=owner.json()))
        with patch('nativepeers.caller.ancestry',return_value=[owner]),patch('nativepeers.caller.native_binaries',return_value=binaries),patch.dict(os.environ,environment):
            with pytest.raises(PeerError) as ambiguous:asyncio.run(dispatch(parser().parse_args(['whoami','--json']),j))
        assert ambiguous.value.details['reason']=='AMBIGUOUS_BINDING'
        with patch('nativepeers.caller.ancestry',return_value=[owner]),patch('nativepeers.caller.native_binaries',return_value=binaries),patch.dict(os.environ,dict(environment,NPEER_BOOTSTRAP=str(live))):
            with pytest.raises(PeerError) as bootstrap:asyncio.run(dispatch(parser().parse_args(['whoami','--json']),j))
        assert bootstrap.value.details['reason']=='OWNER_GONE'
        with patch('nativepeers.caller.ancestry',return_value=[owner]),patch('nativepeers.caller.native_binaries',return_value={Path(owner.executable):'grok'}),patch.dict(os.environ,environment):
            with pytest.raises(PeerError) as grok:asyncio.run(dispatch(parser().parse_args(['whoami','--json']),j))
        assert grok.value.details['reason']=='OWNER_GONE'
    finally:j.close()


def test_missing_configured_binding_is_structured_in_cli_and_mcp(tmp_path,capsys):
    import asyncio,json
    from types import SimpleNamespace
    from nativepeers.cli import main
    from nativepeers.mcp_server import Facade
    j=Journal(tmp_path/'state');missing=j.root/'bindings/missing'
    try:
        with patch.dict(os.environ,{'NPEER_BINDING':str(missing),'NPEER_HOME':str(j.root)}),patch('sys.argv',['npeer','whoami','--json']):
            with pytest.raises(SystemExit) as exit:main()
            assert exit.value.code!=0
            reply=json.loads(capsys.readouterr().out)
            assert reply['error']['code']=='CALLER_UNBOUND'
            assert reply['error']['details']['reason']=='BINDING_MISSING'
            for facade in (Facade(j,None),Facade(j,missing)):
                result=asyncio.run(facade.call(None,SimpleNamespace(name='usage_status',arguments={})))
                error=json.loads(result.content[0].text)['error']
                assert error['code']=='CALLER_UNBOUND' and error['details']['reason']=='BINDING_MISSING'
                assert 'normal native' in error['details']['next_binding_event']
    finally:j.close()
