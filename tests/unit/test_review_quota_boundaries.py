import asyncio
import json
import os
import time
from unittest.mock import AsyncMock, Mock, patch

import pytest

from nativepeers.errors import PeerError
from nativepeers.files import atomic_json, canonical
from nativepeers.fallback import remember_limit, scope_limit
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.usage import store
from nativepeers.usage.normalize import pool
from nativepeers.usage.service import Usage
from nativepeers.worker_process import Worker


def worker(j,home,group=None,model='sonnet'):
    group=group or new_id();wid,task,native=[new_id() for _ in range(3)]
    j.db.execute('INSERT OR IGNORE INTO groups VALUES(?,?,?,?,?)',(group,'parent','{}','RUNNING','{}'))
    spec={'provider':'claude','title':wid,'model':model,'max_task_turns':8,'wall_time_seconds':5}
    lease={'provider_home':str(home.resolve()),'spec':spec,'binding':str(j.root/'bindings'/wid),'parent_key':'parent','expires_at':time.time()+30}
    j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(wid,group,None,'claude',native,wid,model,None,None,str(home),None,'READY',1,canonical(lease).decode()))
    j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(task,wid,None,None,'QUEUED',None,None,1,'{"initial":true}'))
    w=Worker(j,wid);w.emit=Mock()
    return w,task


def test_group_suppression_keeps_model_pool_reset_and_policy_scope(tmp_path):
    j=Journal(tmp_path/'state');home=tmp_path.resolve()
    try:
        sonnet,task=worker(j,home);opus,_=worker(j,home,sonnet.row['group_id'],'opus')
        error={'code':'QUOTA_EXHAUSTED','message':'Sonnet weekly exhausted','execution':{'definitive_no_execution':True},
            'details':{'usage':{'pools':[dict(pool('weekly-sonnet',remaining=0,models=['sonnet'],reset=time.time()+100),state='EXHAUSTED')]}}}
        remember_limit(j,sonnet.row,home,error)
        assert scope_limit(j,sonnet.row,home,task)['suppressed']
        assert scope_limit(j,opus.row,home) is None
        store.record(j,'claude',home,{'source':'native','pools':[pool('weekly-sonnet',remaining=80,models=['sonnet'])]})
        assert scope_limit(j,sonnet.row,home,task) is None
        error['details']['usage']['pools'][0].update(pool_id='global',models=['*'])
        remember_limit(j,sonnet.row,home,error)
        assert scope_limit(j,opus.row,home)['suppressed']
        opus.lease['spec']['quota_policy']='warn';opus.row['lease_json']=canonical(opus.lease).decode()
        assert scope_limit(j,opus.row,home) is None
        error['details']['usage']['pools'][0]['resets_at']=time.time()-1
        remember_limit(j,sonnet.row,home,error)
        assert scope_limit(j,opus.row,home) is None
    finally:j.close()


@pytest.mark.parametrize('discover_later',[False,True])
def test_task_refresh_reserves_newly_discovered_pool_before_input(tmp_path,discover_later):
    async def run():
        j=Journal(tmp_path/'state');home=tmp_path.resolve();entered=asyncio.Event();release=asyncio.Event();calls=[]
        atomic_json(j.root/'policy.json',{'usage':{'pool_concurrency':{'claude:shared':1}}})
        first,a=worker(j,home);second,b=worker(j,home,first.row['group_id'])
        refreshes=0
        async def refresh(*args,**kwargs):
            nonlocal refreshes
            refreshes+=1
            if discover_later and refreshes==1:return
            store.record(j,'claude',home,{'source':'native','pools':[pool('shared',remaining=90,models=['*'])]})
        async def turn(text):
            calls.append(text);entered.set();await release.wait();return {'status':'completed','text':'done'}
        first.claude_turn=turn;second.claude_turn=turn
        try:
            with patch.object(Usage,'refresh',new=refresh):
                pending=asyncio.create_task(first.task(a,'first'))
                await asyncio.wait_for(entered.wait(),2)
                await asyncio.wait_for(second.task(b,'second'),2)
                assert j.one('SELECT state FROM tasks WHERE task_id=?',(b,))['state']=='FAILED'
                artifact=j.one('SELECT result_artifact_id FROM tasks WHERE task_id=?',(b,))['result_artifact_id']
                error=json.loads((j.root/'artifacts'/artifact).read_text())['error']
                assert error['code']=='CAPACITY_UNAVAILABLE' and not error['may_have_received'],error
                assert len(calls)==1
                # Rechecking the admitted worker counts its own demand once.
                with j.transaction():Usage(j).admit_local_demand(first.lease,worker_id=first.row['worker_id'])
                release.set();await pending
        finally:
            release.set()
            if 'pending' in locals():await asyncio.gather(pending,return_exceptions=True)
            j.close()
    asyncio.run(run())

def test_home_labels_share_canonical_and_legacy_resolution(tmp_path):
    from nativepeers.registry import Registry
    from nativepeers.process import identity
    from nativepeers.models import home_id
    from nativepeers.caller import Caller
    j=Journal(tmp_path/'state');home=tmp_path/'home';home.mkdir();link=tmp_path/'alias';link.symlink_to(home)
    try:
        atomic_json(j.root/'policy.json',{'home_labels':{str(link):'WORK'}})
        peer=Registry(j).register('codex',home,new_id(),'fixture',identity(os.getpid()).json(),tmp_path,{'kind':'fixture'})
        assert peer['home_label']=='WORK' and Registry(j).resolve('codex@WORK:fixture')['home']==str(home)
        assert Usage(j).home('codex',label='WORK')==home
        assert Usage(j).home('codex',Caller('peer',peer['handle'],peer['incarnation_id']),'WORK')==home
        atomic_json(j.root/'policy.json',{'home_labels':{str(home):'WORK',home_id(home):'CONFLICT'}})
        with pytest.raises(PeerError) as e:Usage(j).home('codex',label='WORK')
        assert e.value.code=='AMBIGUOUS_TARGET'
    finally:j.close()


def test_suppression_and_cached_guard_keep_original_measurement_age(tmp_path):
    j=Journal(tmp_path/'state');home=tmp_path.resolve();start=time.time()
    atomic_json(j.root/'policy.json',{'usage':{'freshness_seconds':120}})
    try:
        w,task=worker(j,home)
        store.record(j,'claude',home,dict(source='native',pools=[pool('weekly',remaining=0,models=['sonnet'])]),at=start)
        with patch('time.time',return_value=start+100):
            with pytest.raises(PeerError) as failure:Usage(j).guard('claude',home,w.spec)
            cached=failure.value.json();cached['execution']=cached['details']['execution']
            remember_limit(j,w.row,home,cached)
            suppressed=scope_limit(j,w.row,home,task)
            assert suppressed['suppressed']
            remember_limit(j,w.row,home,suppressed)
        proof=json.loads(j.one('SELECT error_json FROM group_provider_limits')['error_json'])['limits'][0]
        assert proof['at']==start
        with patch('time.time',return_value=start+150):assert scope_limit(j,w.row,home,task) is None
    finally:j.close()


def test_passive_wrapper_cannot_clear_a_newer_matching_refusal(tmp_path):
    j=Journal(tmp_path/'state');home=tmp_path.resolve();now=time.time()
    try:
        original=pool('weekly',remaining=80,models=['sonnet'],observed_at=now-20)
        store.record(j,'claude',home,dict(source='native',pools=[original]),at=now-20)
        store.record(j,'claude',home,dict(source='native-task-error',pools=[pool('task-refusal/sonnet',state='EXHAUSTED',models=['sonnet'],limiting_scope='weekly')]),kind='native-error',at=now-10)
        for extra in ({'model':{'id':'new'}},{'pools':[original,pool('other',remaining=90)]}):
            store.record(j,'claude',home,dict(source='passive',pools=[original],**{k:v for k,v in extra.items() if k!='pools'}) if 'pools' not in extra else dict(source='passive',**extra),at=now,dedupe=True)
            assert Usage(j).assessment('claude',home,'sonnet')['blockable']
        store.record(j,'claude',home,dict(source='native',pools=[pool('weekly',remaining=80,models=['sonnet'])]),at=now+1)
        assert not Usage(j).snapshot('claude',home,model='sonnet',now=now+1)['models'][0]['state']=='EXHAUSTED'
    finally:j.close()


@pytest.mark.parametrize('scope',['*','requested-exact'])
def test_exact_model_is_assessed_beyond_display_limit(tmp_path,scope):
    j=Journal(tmp_path/'state');home=tmp_path.resolve();wanted='requested-exact'
    try:
        store.record(j,'codex',home,dict(source='native',models=[{'model':'catalog-'+str(i)} for i in range(65)],pools=[pool('limit',remaining=0,models=[scope])]))
        assert len(Usage(j).snapshot('codex',home)['models'])==64
        view=Usage(j).snapshot('codex',home,model=wanted)
        assert [m['model'] for m in view['models']]==[wanted]
        assert Usage(j).assessment('codex',home,wanted)['blockable']
    finally:j.close()


def test_current_labels_change_without_rebinding_identity(tmp_path):
    from nativepeers.registry import Registry
    from nativepeers.process import identity
    from nativepeers.models import home_id
    j=Journal(tmp_path/'state');home=tmp_path.resolve();r=Registry(j)
    try:
        atomic_json(j.root/'policy.json',{'home_labels':{str(home):'WORK'}})
        peer=r.register('codex',home,new_id(),'fixture',identity(os.getpid()).json(),home,{'kind':'fixture'})
        atomic_json(j.root/'policy.json',{'home_labels':{str(home):'PERSONAL'}})
        current=r.resolve('codex@PERSONAL:fixture')
        assert (current['handle'],current['incarnation_id'])==(peer['handle'],peer['incarnation_id'])
        assert Usage(j).home('codex',label='PERSONAL')==home
        with pytest.raises(PeerError):r.resolve('codex@WORK:fixture')
        atomic_json(j.root/'policy.json',{'home_labels':{str(home):'PERSONAL',home_id(home):'WORK'}})
        for operation in (lambda:r.resolve('codex@WORK:fixture'),lambda:Usage(j).home('codex',label='WORK')):
            with pytest.raises(PeerError) as error:operation()
            assert error.value.code=='AMBIGUOUS_TARGET'
    finally:j.close()


@pytest.mark.parametrize('explicit',[False,True])
def test_owned_task_refresh_uses_recorded_claude_layout(tmp_path,explicit):
    from nativepeers.usage.store import _home_layout
    j=Journal(tmp_path/'state');home=tmp_path.resolve();w,task=worker(j,home)
    w.lease['provider_home_explicit']=explicit
    async def refresh(*args,**kwargs):assert _home_layout.get() is explicit
    w.claude_turn=AsyncMock(return_value={'status':'completed','text':'done'})
    try:
        with patch.object(Usage,'refresh',new=refresh):asyncio.run(w.task(task,'fixture'))
        w.claude_turn.assert_awaited_once()
        assert _home_layout.get() is None
    finally:j.close()


@pytest.mark.parametrize('alias,model,expected',[('fable','claude-fable-5-1',True),('opusplan','claude-sonnet-5-5',True),('best','claude-opus-5-5',False),('default','claude-opus-5-5',False),('sonnet','claude-opus-5-5',False)])
def test_usage_aliases_require_deterministic_known_family(alias,model,expected):
    from nativepeers.usage.service import same_model
    assert same_model('claude',alias,model) is expected


def test_opt_in_selection_explanation_is_shared_by_cli_and_mcp(tmp_path):
    from nativepeers.caller import Caller
    from nativepeers.cli import parser,dispatch
    from nativepeers.core import Core
    j=Journal(tmp_path/'state');home=tmp_path.resolve();w,_=worker(j,home);caller=Caller('peer','parent','fixture')
    try:
        store.record(j,'claude',home,dict(source='native',pools=[pool('weekly',remaining=40,models=['sonnet'])]))
        now=time.time();before=j.db.total_changes
        with patch('time.time',return_value=now),patch('nativepeers.cli.bind_caller',return_value=caller):
            cli=asyncio.run(dispatch(parser().parse_args(['status',w.row['group_id'],'--explain-selection','--json']),j))
            mcp=asyncio.run(Core(j).tool(caller,'task_status',{'handle':w.row['group_id'],'explain_selection':True}))
        assert cli==mcp and j.db.total_changes==before
        selected=cli['workers'][0]['selection']
        assert selected['home']==str(home) and selected['requested_model']=='sonnet'
        assert selected['quota']['pools'][0]['observed_at'] is not None
        assert selected['vendor_quota_reserved'] is False
        assert not cli['workers'][0]['release']['released']
        plain=asyncio.run(Core(j).status(caller,w.row['group_id']))
        assert 'selection' not in plain['workers'][0]
    finally:j.close()


def test_newer_measurement_for_other_model_does_not_clear_refusal(tmp_path):
    j=Journal(tmp_path/'state');home=tmp_path.resolve();now=time.time()
    try:
        store.record(j,'claude',home,dict(source='native-task-error',pools=[pool('refusal',state='EXHAUSTED',models=['sonnet'],limiting_scope='weekly')]),kind='native-error',at=now-10)
        store.record(j,'claude',home,dict(source='native',pools=[pool('weekly',remaining=80,models=['opus'])]),at=now)
        assert Usage(j).assessment('claude',home,'sonnet')['blockable']
    finally:j.close()
