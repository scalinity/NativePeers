import json
import time

import pytest
from hypothesis import given,settings,strategies as st
from jsonschema import validate

from nativepeers.caller import Caller
from nativepeers.errors import PeerError
from nativepeers.journal import Journal
from nativepeers.usage.import_snapshot import import_snapshot
from nativepeers.usage.normalize import pool,aggregate,assess_pool
from nativepeers.usage.config import DEFAULTS
from nativepeers.usage.service import Usage
from nativepeers.usage import store


def test_import_is_deliberate_bounded_lower_priority_and_forbidden_to_workers(tmp_path,monkeypatch):
    j=Journal(tmp_path/'state');home=tmp_path/'home';monkeypatch.setattr('nativepeers.usage.service.provider_home',lambda p:home)
    value={'schema_version':1,'provider':'claude','observed_at':time.time(),'models':['opus'],'pools':[{'pool_id':'manual','remaining':0,'unit':'requests','models':['opus']}]}
    try:
        with pytest.raises(PeerError):import_snapshot(j,Caller('peer','worker',grants={'worker_id':'owned'}),value)
        result=import_snapshot(j,Caller('external-local','local'),value)
        assert not result['provider_verified']
        service=Usage(j);assert not service.assessment('claude',home,'opus')['blockable']
        store.record(j,'claude',home,{'source':'native','pools':[pool('native',remaining=50,unit='requests',models=['*'])],'models':[]})
        assert service.assessment('claude',home,'opus')['state']=='AVAILABLE'
        with pytest.raises(PeerError):import_snapshot(j,Caller('external-local','local'),dict(value,credentials='never accepted'))
        with pytest.raises(PeerError):import_snapshot(j,Caller('external-local','local'),dict(value,observed_at=time.time()+100))
        data={'schema_version':1,'providers':[service.snapshot('claude',home)],'inference_submitted':False,'quota_reserved':False,'local_usage':{}}
        from pathlib import Path
        validate(data,json.loads((Path(__file__).parents[2]/'schemas/usage.v1.json').read_text()))
    finally:j.close()


def test_native_refusal_remains_authoritative_beside_manual_snapshot(tmp_path):
    j=Journal(tmp_path/'state');home=tmp_path/'home'
    try:
        store.record(j,'claude',home,{'source':'import','pools':[dict(pool('manual',remaining=90,models=['*']),basis='user-supplied')],'models':[]},kind='import')
        store.native_error(j,{'provider':'claude','model_requested':'opus'},home,{'code':'QUOTA_EXHAUSTED','may_have_received':True})
        assert Usage(j).assessment('claude',home,'opus')['blockable']
    finally:j.close()


@settings(max_examples=30)
@given(st.lists(st.floats(min_value=0,max_value=99,allow_nan=False,allow_infinity=False),min_size=1,max_size=10))
def test_applicable_exhaustion_cannot_be_hidden_by_healthy_windows(values):
    healthy=[assess_pool(pool(str(i),used=x,models=['*']),100,120,100,DEFAULTS) for i,x in enumerate(values)]
    exhausted=assess_pool(pool('limiting',remaining=0,models=['*']),100,120,100,DEFAULTS)
    assert aggregate([*healthy,exhausted])=='EXHAUSTED'
    assert aggregate([exhausted,*reversed(healthy)])=='EXHAUSTED'
