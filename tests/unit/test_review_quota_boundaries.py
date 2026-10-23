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


def test_task_refresh_reserves_newly_discovered_pool_before_input(tmp_path):
    async def run():
        j=Journal(tmp_path/'state');home=tmp_path.resolve();entered=asyncio.Event();release=asyncio.Event();calls=[]
        atomic_json(j.root/'policy.json',{'usage':{'pool_concurrency':{'claude:shared':1}}})
        first,a=worker(j,home);second,b=worker(j,home,first.row['group_id'])
        async def refresh(*args,**kwargs):
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
