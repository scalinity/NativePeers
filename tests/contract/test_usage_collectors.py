import asyncio
import json
from pathlib import Path
import shlex
import sys
import time
from unittest.mock import patch

import pytest

from nativepeers.compatibility import fingerprint
from nativepeers.files import atomic_json
from nativepeers.journal import Journal
from nativepeers.usage.collectors import CodexCollector
from nativepeers.usage.service import Usage
from nativepeers.usage import store


@pytest.mark.parametrize("catalog_available",[True,False])
def test_real_read_only_rpc_transport_has_no_inference_or_account_mutation(tmp_path,catalog_available):
    j=Journal(tmp_path/'state');home=tmp_path/'native-home';home.mkdir();log=tmp_path/'calls'
    binary=tmp_path/'provider'
    binary.write_text('#!'+sys.executable+'\n'+'''
import json,os,sys
from pathlib import Path
for line in sys.stdin:
 x=json.loads(line);m=x['method']
 with Path('''+repr(str(log))+''').open('a') as f:f.write(m+'\\n')
 if m=='initialized':continue
 assert m in ('initialize','account/read','account/rateLimits/read','model/list'),m
 assert os.environ['CODEX_HOME']=='''+repr(str(home))+'''
 if m=='initialize':r={}
 elif m=='account/read':
  assert x['params']['refreshToken'] is False
  r={'account':{'type':'chatgpt','email':'PRIVATE_EMAIL','id':'PRIVATE_ACCOUNT'}}
 elif m=='account/rateLimits/read':
  assert x['params']['excludeResetCreditDetails'] is True
  r={'rateLimits':{'limitId':'native','primary':{'usedPercent':20,'windowDurationMins':300}},'accountId':'PRIVATE_ACCOUNT'}
 else:r={'data':[{'model':'native-model','displayName':'Native','supportedReasoningEfforts':[{'reasoningEffort':'xhigh'}],'inputModalities':['text']}],'nextCursor':None}
 if m=='model/list' and not '''+repr(catalog_available)+''':
  print(json.dumps({'id':x['id'],'error':{'code':-32601,'message':'Optional catalog unavailable'}}),flush=True)
 else:print(json.dumps({'id':x['id'],'result':r}),flush=True)
''');binary.chmod(0o700)
    atomic_json(j.root/'compat/runtime.json',{'providers':{'codex':fingerprint(binary)}})
    try:
        with patch('nativepeers.usage.collectors.executable',return_value=binary):
            result=asyncio.run(CodexCollector().collect(j,home))
        assert result['pools'][0]['used']==20
        assert result['models'][0]['efforts']==['xhigh'] if catalog_available else result['models']==[]
        assert 'PRIVATE_' not in json.dumps(result)
        calls=log.read_text().splitlines()
        assert calls==['initialize','initialized','account/read','account/rateLimits/read','model/list']
    finally:j.close()


def test_cross_connection_single_flight_and_cancelled_waiter_release_lock(tmp_path):
    journals=[Journal(tmp_path/'state') for _ in range(3)];home=tmp_path/'home'
    async def run():
        started=asyncio.Event();finish=asyncio.Event();calls=0
        async def collect(j,h):
            nonlocal calls
            calls+=1;started.set();await finish.wait()
            return dict(source='fixture',pools=[],models=[])
        with patch('nativepeers.usage.service.COLLECTORS',{'codex':type('C',(),{'collect':staticmethod(collect)})()}):
            first=asyncio.create_task(Usage(journals[0]).refresh('codex',home,force=True));await started.wait()
            second=asyncio.create_task(Usage(journals[1]).refresh('codex',home,force=True))
            third=asyncio.create_task(Usage(journals[2]).refresh('codex',home,force=True))
            await asyncio.sleep(.05);second.cancel()
            with pytest.raises(asyncio.CancelledError):await second
            finish.set();await asyncio.wait_for(asyncio.gather(first,third),2)
        assert calls==1
    try:asyncio.run(run())
    finally:
        for j in journals:j.close()


def test_collector_deadline_reaps_owned_fake_provider(tmp_path):
    from nativepeers.adapters.codex_app_server import AppServer
    j=Journal(tmp_path/'state');binary=tmp_path/'provider';pid=tmp_path/'pid'
    binary.write_text('#!'+sys.executable+'\nimport os,signal\nfrom pathlib import Path\nPath('+repr(str(pid))+').write_text(str(os.getpid()))\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\nsignal.pause()\n');binary.chmod(0o700)
    atomic_json(j.root/'compat/runtime.json',{'providers':{'codex':fingerprint(binary)}})
    original=AppServer.start
    async def short_start(self):return await asyncio.wait_for(original(self),.2)
    started=time.monotonic()
    try:
        with patch('nativepeers.usage.collectors.executable',return_value=binary),patch.object(AppServer,'start',short_start):
            with pytest.raises(TimeoutError):asyncio.run(CodexCollector().collect(j,tmp_path/'home'))
        import os
        with pytest.raises(ProcessLookupError):os.kill(int(pid.read_text()),0)
        assert time.monotonic()-started<4
    finally:j.close()


def test_crashed_collector_cannot_publish_half_snapshot(tmp_path):
    j=Journal(tmp_path/'state');service=Usage(j);home=tmp_path/'home'
    async def broken(*a):raise ConnectionError('not a complete response')
    try:
        with patch('nativepeers.usage.service.COLLECTORS',{'codex':type('C',(),{'collect':staticmethod(broken)})()}):
            asyncio.run(service.refresh('codex',home,force=True))
        assert store.latest(j,'codex',home,'snapshot') is None
        assert service.snapshot('codex',home)['state']=='UNKNOWN'
    finally:j.close()


def test_sparse_native_update_preserves_other_pool_age_and_separates_turn_tokens(tmp_path):
    from nativepeers.usage.events import codex_update,codex_tokens
    from nativepeers.usage.normalize import pool
    j=Journal(tmp_path/'state');home=tmp_path/'home';old=time.time()-130
    try:
        store.record(j,'codex',home,dict(source='codex-rpc',pools=[
            pool('shared/primary',used=10),pool('shared/secondary',used=80)],models=[]),at=old)
        codex_update(j,home,{'rateLimits':{'limitId':'shared','primary':{'usedPercent':20}},'accountId':'PRIVATE_ID'},None)
        view=Usage(j).snapshot('codex',home)
        first,second=view['pools']
        assert first['used']==20 and first['freshness']=='FRESH'
        assert second['used']==80 and second['freshness']=='STALE'
        assert 'PRIVATE_ID' not in json.dumps(store.latest(j,'codex',home))
        assert codex_tokens({'tokenUsage':{'last':{'inputTokens':11,'outputTokens':3},'total':{'inputTokens':1000}}})=={'input_tokens':11,'output_tokens':3}
        assert len(j.all('SELECT * FROM task_usage'))==0
    finally:j.close()
