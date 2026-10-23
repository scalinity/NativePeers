import json
import time

import pytest

from nativepeers.journal import Journal
from nativepeers.usage.config import validate
from nativepeers.usage.forecast import estimate,local_summary,record_task,recommend,runway
from nativepeers.usage.normalize import pool
from nativepeers.usage.service import Usage,format_summary
from nativepeers.usage import store


def test_local_consumption_is_idempotent_and_not_account_quota(tmp_path):
    j=Journal(tmp_path/'state');home=tmp_path/'home'
    row={'worker_id':'worker','provider':'codex'};spec={'permission_profile':'analysis','reasoning_effort':'xhigh','requirements':{'task_type':'review'}}
    try:
        for i in range(5):
            result={'task_id':str(i),'model_requested':'exact','status':'completed','usage':{'input_tokens':100+i,'output_tokens':20,'secret':'DO_NOT_STORE'},'started_at':i*20,'completed_at':i*20+10,'native_turn_ids':['turn-'+str(i)]}
            record_task(j,row,spec,result,0,home);record_task(j,row,spec,result,0,home)
        summary=local_summary(j)
        assert summary['bounded_attempts']==5 and summary['models'][0]['input_tokens']==510
        prediction=estimate(j,'codex',home,'exact','xhigh',spec['requirements'],'analysis')
        assert prediction['state']=='ESTIMATE' and prediction['median_tokens']==122
        assert prediction['quota_consumption'].startswith('UNKNOWN')
        assert estimate(j,'codex',home,'exact','medium',spec['requirements'],'analysis')['state']=='UNKNOWN'
        assert 'DO_NOT_STORE' not in str(j.all('SELECT payload_json FROM task_usage'))
    finally:j.close()


def test_account_pace_requires_compatible_observations_and_preserves_attribution(tmp_path):
    j=Journal(tmp_path/'state');home=tmp_path/'home';now=time.time();reset=now+1000
    try:
        for offset,used in [(-120,10),(-60,12),(0,14)]:
            store.record(j,'codex',home,dict(source='native',pools=[pool('shared',used=used,reset=reset,resolution=1,duration=18000,models=['*'])],models=[]),at=now+offset)
        view=Usage(j).snapshot('codex',home,now=now)
        forecast=view['pools'][0]['runway']
        assert forecast['state']=='ESTIMATE' and forecast['seconds_at_recent_pace']==pytest.approx(2580)
        assert not forecast['task_attribution']
        store.record(j,'codex',home,dict(source='native',pools=[pool('shared',used=0,reset=reset+1000,resolution=1,models=['*'])],models=[]),at=now+1)
        assert Usage(j).snapshot('codex',home,now=now+1)['pools'][0]['runway']['state']=='UNKNOWN'
    finally:j.close()


def test_recommendations_preserve_quality_exact_choice_and_explain_unknowns():
    settings=validate({'models':[
        {'provider':'codex','model':'strong','source':'explicit fixture preference','quality':{'architecture':'demanding'},'vendor':'openai','context_tokens':1000},
        {'provider':'antigravity','model':'efficient','source':'explicit fixture preference','quality':{'architecture':'routine'},'vendor':'google','context_tokens':1000}]})
    status={'providers':[{'provider':'codex','models':[{'model':'strong','state':'UNKNOWN','pool_ids':[],'support':'NATIVE_CATALOG'}],'pools':[]},
        {'provider':'antigravity','models':[{'model':'efficient','state':'AVAILABLE','pool_ids':[],'support':'NATIVE_CATALOG'}],'pools':[]}]}
    result=recommend(status,{'task_type':'architecture','quality':'demanding','context_tokens':500},'quota-preserving',settings)
    assert result['recommended']['model']=='strong' and not result['selection_changed']
    assert result['excluded'][0]['model']=='efficient'
    assert 'source:' in result['recommended']['reasons'][0]
    assert recommend(status,{'task_type':'architecture','quality':'demanding','independent_from_vendor':'openai'},'balanced',settings)['recommended'] is None
    assert recommend(status,{'task_type':'architecture','quality':'routine','model':'efficient'},'exact-model-only',settings)['recommended']['model']=='efficient'
    assert status['providers'][0]['models'][0]['model']=='strong'


@pytest.mark.parametrize('case',['future','unknown-reset','source-change','precision-change','backwards'])
def test_account_pace_rejects_unproven_history(tmp_path,case):
    j=Journal(tmp_path/'state');home=tmp_path/'home';now=time.time();reset=None if case=='unknown-reset' else now+1000
    try:
        for index,(offset,used) in enumerate([(-120,10),(-60,12),(0,14)]):
            if case=='future' and index==1:offset=30
            if case=='backwards' and index==1:used=18
            source='other' if case=='source-change' and index==0 else 'native'
            resolution=2 if case=='precision-change' and index==0 else 1
            store.record(j,'codex',home,dict(source=source,pools=[pool('shared',used=used,reset=reset,resolution=resolution,models=['*'])],models=[]),at=now+offset)
        assert Usage(j).snapshot('codex',home,now=now)['pools'][0]['runway']['state']=='UNKNOWN'
    finally:j.close()


@pytest.mark.parametrize('change',['duration','type','unknown'])
def test_account_pace_rejects_changed_or_unknown_window_identity(tmp_path,change):
    j=Journal(tmp_path/'state');home=tmp_path/'home';now=time.time();reset=now+1000
    try:
        for index,(offset,used) in enumerate([(-120,10),(-60,12),(0,14)]):
            p=pool('shared',used=used,reset=reset,resolution=1,duration=604800,models=['*'])
            if index==2:
                if change=='duration':p['window_seconds']=18000
                elif change=='type':p['window_type']='rolling'
                else:p['window_type']='unknown'
            store.record(j,'codex',home,dict(source='native',pools=[p]),at=now+offset)
        assert Usage(j).snapshot('codex',home,now=now)['pools'][0]['runway']['state']=='UNKNOWN'
    finally:j.close()
