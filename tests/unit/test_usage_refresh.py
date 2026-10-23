import asyncio
import json
import time
from unittest.mock import patch

import pytest

from nativepeers.errors import PeerError
from nativepeers.journal import Journal
from nativepeers.usage import store
from nativepeers.usage.normalize import antigravity,claude,pool
from nativepeers.usage.service import Usage


@pytest.mark.parametrize('intervening',['catalog','refresh','passive'])
def test_non_acquisition_observations_never_skip_quota_fetch(tmp_path,intervening):
    j=Journal(tmp_path/'state');home=tmp_path/'home';service=Usage(j);calls=[]
    async def collect(*args):calls.append(1);return {'source':'native','pools':[pool('weekly',used=20)],'models':[]}
    try:
        if intervening=='passive':store.record(j,'codex',home,{'source':'statusline','pools':[pool('weekly',used=20)],'models':[]})
        else:store.record(j,'codex',home,{'code':'failure','models':[]},kind=intervening)
        with patch('nativepeers.usage.service.COLLECTORS',{'codex':type('C',(),{'collect':staticmethod(collect)})()}):asyncio.run(service.refresh('codex',home))
        assert len(calls)==1 and store.latest(j,'codex',home,'snapshot',active=True)
    finally:j.close()


@pytest.mark.parametrize('age,reset',[(130,None),(-20,None),(0,-1)])
def test_reset_and_clock_staleness_force_reacquisition(tmp_path,age,reset):
    j=Journal(tmp_path/'state');home=tmp_path/'home';calls=[];now=time.time()
    async def collect(*args):calls.append(1);return {'pools':[pool('weekly',used=10)],'models':[]}
    try:
        store.record(j,'codex',home,{'acquisition_id':'old','collection':{'outcome':'FRESH'},'pools':[pool('weekly',used=100,reset=now+reset if reset else None)],'models':[]},at=now-age)
        with patch('nativepeers.usage.service.COLLECTORS',{'codex':type('C',(),{'collect':staticmethod(collect)})()}):asyncio.run(Usage(j).refresh('codex',home))
        assert calls==[1]
    finally:j.close()


def test_source_denial_does_not_log_out_native_model(tmp_path):
    j=Journal(tmp_path/'state');home=tmp_path/'home'
    async def collect(*args):raise PeerError('AUTH_REQUIRED','Source could not authenticate quota read')
    try:
        store.record(j,'claude',home,{'pools':[pool('weekly',used=10,models=['*'])],'models':[{'model':'opus'}]})
        with patch('nativepeers.usage.service.COLLECTORS',{'claude':type('C',(),{'collect':staticmethod(collect)})()}):asyncio.run(Usage(j).refresh('claude',home,force=True))
        view=Usage(j).snapshot('claude',home)
        assert view['collector_state']=='SOURCE_ACCESS_DENIED'
        assert view['state']==view['models'][0]['state']=='AVAILABLE'
    finally:j.close()


def test_only_exact_newer_scope_reconciles_refusal(tmp_path):
    j=Journal(tmp_path/'state');home=tmp_path/'home';now=time.time()
    row={'provider':'claude','model_requested':'opus'}
    try:
        store.native_error(j,row,home,{'code':'QUOTA_EXHAUSTED','limiting_scope':'weekly'})
        store.record(j,'claude',home,{'pools':[pool('short',used=10,models=['*'])],'models':[]},at=now+1)
        assert Usage(j).assessment('claude',home,'opus')['blockable']
        store.record(j,'claude',home,{'pools':[pool('weekly',used=10,models=['*'])],'models':[]},at=now+2)
        assert not Usage(j).assessment('claude',home,'opus')['blockable']
        store.native_error(j,row,home,{'code':'QUOTA_EXHAUSTED'})
        assert Usage(j).assessment('claude',home,'opus')['blockable']
    finally:j.close()


def test_disabled_amount_and_gateway_overage_keep_native_semantics():
    values=antigravity({'quota':{'off':{'remaining_fraction':0,'disabled':True},'amount':{'remaining_amount':12}}})
    assert not values[0]['limiting'] and values[0]['disabled']
    from nativepeers.usage.normalize import assess_pool
    from nativepeers.usage.config import DEFAULTS
    assert assess_pool(values[0],1,120,1,DEFAULTS)['warnings']==[]
    assert values[1]['unit']=='native-amount' and values[1]['remaining']==12 and values[1]['used'] is None
    p=claude({'rate_limits':{'spend_limit':{'used_percentage':125}}})[0]
    assert p['used']==125 and p['remaining']==0


def test_forced_waiter_does_not_coalesce_with_catalog_or_passive_update(tmp_path):
    from nativepeers.usage.service import refresh_lock
    from nativepeers.files import sha256
    j=Journal(tmp_path/'state');home=tmp_path/'home';calls=[]
    async def collect(*args):calls.append(1);return {'pools':[pool('weekly',used=10)],'models':[]}
    async def run():
        key=sha256(['codex',str(home),store.scope('codex',home)])
        with patch('nativepeers.usage.service.COLLECTORS',{'codex':type('C',(),{'collect':staticmethod(collect)})()}):
            async with refresh_lock(j.root/'locks'/('usage-'+key+'.lock')):
                waiter=asyncio.create_task(Usage(j).refresh('codex',home,force=True))
                await asyncio.sleep(.04)
                store.record(j,'codex',home,{'pools':[pool('weekly',used=20)],'models':[]})
                store.record(j,'codex',home,{'models':[]},kind='catalog')
            await asyncio.wait_for(waiter,1)
    try:asyncio.run(run());assert calls==[1]
    finally:j.close()
