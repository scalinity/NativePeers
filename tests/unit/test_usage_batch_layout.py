"""Retained batch selections govern planning, recommendations and admission."""
import asyncio
from contextlib import closing
import json
import os
import time
from unittest.mock import AsyncMock,patch

import pytest

from nativepeers.caller import Caller
from nativepeers.errors import PeerError
from nativepeers.files import atomic_json,canonical
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.recovery import resume_worker
from nativepeers.supervisor import Group
from nativepeers.usage import store
from nativepeers.usage.normalize import pool
from nativepeers.usage.service import Usage


def retained(j,home,explicit,state='READY'):
    group,wid,native=[new_id() for _ in range(3)]
    policy={'initial_tasks':[],'completion_mode':'wait','parent_incarnation':'fixture'}
    spec={'provider':'claude','model':'sonnet','title':wid,'permission_profile':'analysis',
          'max_task_turns':4,'wall_time_seconds':60,'requirements':{'task_type':'review'}}
    lease={'provider_home':str(home),'provider_home_explicit':explicit,'spec':spec,
           'grants':{'depth':1},'parent_key':'parent','expires_at':time.time()+60}
    j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(group,'parent',canonical(policy).decode(),'RUNNING','{}'))
    j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (wid,group,'peer-'+wid,'claude',native,wid,'sonnet',None,None,str(home),None,state,1,canonical(lease).decode()))
    return j.one('SELECT * FROM workers WHERE worker_id=?',(wid,)),lease


def observed(j,home,explicit,remaining=80):
    with store.home_layout(explicit):
        store.record(j,'claude',home,{'source':'native','models':[{'model':'sonnet','support':'OBSERVED_NATIVE'}],
            'pools':[pool('shared',remaining=remaining,models=['*'])]})


def policy(j):
    atomic_json(j.root/'policy.json',{'usage':{'pool_concurrency':{'claude:shared':1},'models':[
        {'provider':'claude','model':'sonnet','source':'fixture preference','quality':{'review':'standard'}}]}})


@pytest.mark.parametrize('explicit',[False,True])
def test_batch_keeps_each_home_and_layout_for_assessment_recommendation_and_demand(tmp_path,monkeypatch,explicit):
    home=tmp_path/'.claude';other=tmp_path/'other';home.mkdir();other.mkdir()
    if explicit:monkeypatch.delenv('CLAUDE_CONFIG_DIR',raising=False)
    else:monkeypatch.setenv('CLAUDE_CONFIG_DIR',str(home))
    with closing(Journal(tmp_path/'state')) as j:
        policy(j)
        leases=[retained(j,home,explicit)[1],retained(j,home,not explicit)[1],retained(j,other,explicit)[1]]
        observed(j,home,explicit);observed(j,home,not explicit,0);observed(j,other,explicit)
        usage=Usage(j);before=j.db.total_changes
        plan=usage.batch([{'lease':lease} for lease in leases])
        assert j.db.total_changes==before
        assert [w['state'] for w in plan['workers']]==['AVAILABLE','EXHAUSTED','AVAILABLE']
        assert len({w['scope'] for w in plan['workers']})==3
        assert len(plan['demands'])==3
        assert {d['demand_key'] for d in plan['demands']}=={key for lease in leases for key in usage.demand_keys(lease)}
        assert all(d['proposed_workers']==1 for d in plan['demands'])
        for item,lease in zip(plan['workers'],leases):
            assert item['provider_home_explicit'] is lease['provider_home_explicit']
            assert len(item['pools'])==1
            if item['state']=='EXHAUSTED':assert item['recommendations']['recommended'] is None
            else:assert item['recommendations']['recommended']['state']=='AVAILABLE'
        # Each layout is a distinct local reservation even at the same path.
        for lease in leases:
            with j.transaction():usage.admit_local_demand(lease)
            assert lease['usage_demand']==usage.demand_keys(lease)
        assert store._home_layout.get() is None


@pytest.mark.parametrize('explicit',[False,True])
@pytest.mark.parametrize('operation',['resume','followup'])
def test_retained_layout_refuses_occupied_pool_before_queue_or_launch(tmp_path,monkeypatch,explicit,operation):
    home=tmp_path/'.claude';home.mkdir()
    if explicit:monkeypatch.delenv('CLAUDE_CONFIG_DIR',raising=False)
    else:monkeypatch.setenv('CLAUDE_CONFIG_DIR',str(home))
    with closing(Journal(tmp_path/'state')) as j:
        policy(j);observed(j,home,explicit)
        active,_=retained(j,home,explicit,'RUNNING')
        j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(new_id(),active['worker_id'],None,None,'RUNNING',None,None,1,'{}'))
        row,lease=retained(j,home,explicit,'RELEASED_RESUMABLE' if operation=='resume' else 'READY')
        caller=Caller('peer','parent','fixture')
        with store.home_layout(explicit):expected_scope=store.scope('claude',home)
        assert Usage(j).batch([{'lease':lease}])['workers'][0]['scope']==expected_scope
        start=AsyncMock(side_effect=AssertionError('No controller launch permitted'))
        send=AsyncMock(side_effect=AssertionError('No control frame permitted'))
        if operation=='resume':
            parent={'owner':identity(os.getpid()).json(),'incarnation_id':'fixture','policy':{'profiles':['analysis']}}
            async def invoke():
                with patch('nativepeers.recovery.assert_released'),patch('nativepeers.recovery.Registry.verify',return_value=parent),patch('nativepeers.recovery.Delegation.ensure_supervisor',start):
                    return await resume_worker(j,caller,row['worker_id'],'followup','wait','retained-layout')
        else:
            message={'message_id':new_id(),'exchange_id':new_id(),'from':{'peer_key':'parent'},
                     'to':{'peer_key':row['peer_key']},'body':{'text':'followup'}}
            j.db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?)',
                (message['message_id'],message['exchange_id'],'parent',row['peer_key'],None,None,canonical(message).decode(),'fixture-digest',time.time()+60,time.time()))
            group=Group(j,row['group_id']);group.send_worker=send
            async def invoke():return await group.enqueue({'worker_id':row['worker_id'],'message':message})
        before={table:j.all('SELECT * FROM '+table) for table in ('workers','tasks','groups','completion_routes','resume_requests','messages')}
        with pytest.raises(PeerError) as error:asyncio.run(invoke())
        assert error.value.code=='CAPACITY_UNAVAILABLE'
        assert {table:j.all('SELECT * FROM '+table) for table in before}==before
        assert not error.value.may_have_received
        start.assert_not_awaited();send.assert_not_awaited()
        # When the existing task finishes, the same request can reserve the
        # correct scope and persists that exact demand. Its retry adds no task.
        j.db.execute("UPDATE tasks SET state='COMPLETED' WHERE worker_id=?",(active['worker_id'],))
        start.side_effect=None;send.side_effect=None
        accepted=asyncio.run(invoke());again=asyncio.run(invoke())
        assert accepted['task_id']==again['task_id'] if operation=='resume' else accepted['external_submission_id']==again['external_submission_id']
        saved=json.loads(j.one('SELECT lease_json FROM workers WHERE worker_id=?',(row['worker_id'],))['lease_json'])
        assert saved['usage_demand']==Usage(j).demand_keys(lease)
        assert len(saved['usage_demand'])==1
        assert len(j.all('SELECT * FROM tasks WHERE worker_id=?',(row['worker_id'],)))==1
        if operation=='resume':start.assert_awaited_once()
        else:send.assert_awaited_once()
