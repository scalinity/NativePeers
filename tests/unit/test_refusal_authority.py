"""Quota evidence may supersede only matching model scope and native provenance."""
import json
import time
from unittest.mock import patch

import pytest

from nativepeers.caller import Caller
from nativepeers.fallback import remember_limit,scope_limit
from nativepeers.journal import Journal
from nativepeers.usage import store
from nativepeers.usage.import_snapshot import import_snapshot
from nativepeers.usage.normalize import pool
from nativepeers.usage.service import Usage


@pytest.mark.parametrize('members,clears',[(['opus'],False),(['sonnet'],True),(['*'],True)])
def test_group_proof_requires_refused_model_membership(tmp_path,members,clears):
    j=Journal(tmp_path/'state');home=tmp_path/'home';now=time.time()
    j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',('group','parent','{}','RUNNING','{}'))
    row=dict(group_id='group',provider='claude',model_requested='sonnet',lease_json=json.dumps({'spec':{'model':'sonnet'}}))
    proof=pool('weekly',used=100,models=['sonnet']);proof.update(state='EXHAUSTED',observed_at=now-10)
    try:
        remember_limit(j,row,home,{'code':'QUOTA_EXHAUSTED','execution':{'definitive_no_execution':True},'details':{'usage':{'pools':[proof]}}})
        store.record(j,'claude',home,{'source':'native','pools':[pool('weekly',used=10,models=members)],'models':[]},at=now)
        assert (scope_limit(j,row,home) is None)==clears
        assert scope_limit(j,dict(row,lease_json=json.dumps({'spec':{'model':'sonnet','quota_policy':'warn'}})),home) is None
    finally:j.close()


def test_import_cannot_clear_retained_named_scope_native_or_group_refusal(tmp_path):
    j=Journal(tmp_path/'state');home=tmp_path/'home';now=time.time()
    j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',('group','parent','{}','RUNNING','{}'))
    row=dict(group_id='group',provider='claude',model_requested='sonnet',lease_json=json.dumps({'spec':{'model':'sonnet'}}))
    try:
        with patch('nativepeers.usage.store.time.time',return_value=now-10):
            error={'code':'QUOTA_EXHAUSTED','limiting_scope':'weekly','execution':{'definitive_no_execution':True}}
            store.native_error(j,row,home,error);remember_limit(j,row,home,error)
        value={'schema_version':1,'provider':'claude','observed_at':now,'models':['sonnet'],
            'pools':[{'pool_id':'weekly','used':10,'unit':'percent','models':['sonnet']}]}
        with patch('nativepeers.usage.service.provider_home',return_value=home):
            imported=import_snapshot(j,Caller('external-local','local'),value)
        assert imported['provider_verified'] is False
        service=Usage(j);assert service.assessment('claude',home,'sonnet')['blockable']
        assert scope_limit(j,row,home)['suppressed']
        store.record(j,'claude',home,{'source':'native','pools':[pool('weekly',used=10,models=['sonnet'])],'models':[]},at=now)
        assert not service.assessment('claude',home,'sonnet')['blockable']
        assert scope_limit(j,row,home) is None
    finally:j.close()
