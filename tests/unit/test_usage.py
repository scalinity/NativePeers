import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock,patch

import pytest

from nativepeers.errors import PeerError
from nativepeers.journal import Journal
from nativepeers.usage import normalize as n,store
from nativepeers.usage.config import DEFAULTS,validate
from nativepeers.usage.service import Usage


@pytest.fixture
def usage(tmp_path):
    journal=Journal(tmp_path/'state')
    yield Usage(journal),tmp_path/'home'
    journal.close()


def snapshot(pools,**kw):
    return dict(source='fixture-native',source_version=1,pools=pools,models=[{'model':'exact'}],
        coverage='partial',route='subscription',**kw)


def test_intersection_credits_and_unknown_membership(usage):
    service,home=usage
    payload=snapshot([n.pool('short',used=20,models=['*']),n.pool('weekly',used=100,models=['exact']),
        n.pool('premium',used=0),n.pool('credits',remaining=0,unit='credits',limiting=False)])
    store.record(service.journal,'codex',home,payload)
    result=service.assessment('codex',home,'exact','xhigh')
    assert result['state']=='EXHAUSTED' and result['blockable']
    assert {p['pool_id'] for p in result['pools']}=={'short','weekly'}
    with pytest.raises(PeerError,match='weekly'):service.guard('codex',home,{'model':'exact'})
    assert service.guard('codex',home,{'model':'exact','quota_policy':'warn'})['model']=='exact'
    assert service.assessment('codex',home,'other')['state']=='UNKNOWN'


def test_unknown_stale_passed_reset_and_clock_regression(usage):
    service,home=usage;now=time.time()
    assert service.assessment('claude',home,'opus')['state']=='UNKNOWN'
    for at,reset in [(now-121,None),(now+20,None),(now-1,now-0.5)]:
        store.record(service.journal,'claude',home,snapshot([n.pool('weekly',used=100,models=['*'],reset=reset)]),at=at)
        result=service.assessment('claude',home,'opus')
        assert result['state']=='STALE' and not result['blockable']


def test_provider_normalization_and_sparse_unknown_data():
    values=n.codex({'rateLimitsByLimitId':{'a':{'primary':{'usedPercent':75,'windowDurationMins':15},
        'secondary':{'usedPercent':100,'resetsAt':100000},'credits':{'hasCredits':False,'unlimited':False,'balance':'0'}}},
        'ordinaryUsageAllowed':True,'rateLimitResetCredits':{'availableCount':2}})
    assert values[0]['remaining']==25 and values[0]['window_seconds']==900 and values[0]['resolution']==1
    assert values[1]['remaining']==0
    assert not next(p for p in values if p['pool_id']=='a/credits')['limiting']
    assert not next(p for p in values if p['pool_id']=='reset-credits')['limiting']
    assert n.claude({'rate_limits':{'five_hour':{'used_percentage':23.5},'seven_day':{'used_percentage':100}}})[0]['remaining']==76.5
    assert n.antigravity({'quota':{'bucket':{'remaining_fraction':.9378,'reset_time':'2026-07-06T07:50:32Z'}}})[0]['remaining']==pytest.approx(93.78)
    for parser in n.PARSERS.values():assert parser({})==[]
    assert n.number(True) is None and n.number(float('nan')) is None and n.number(float('inf')) is None
    assert n.timestamp('2026-01-01') is None
    assert 'EST' in n.eastern(n.timestamp('2026-01-15T12:00:00Z'))
    assert 'EDT' in n.eastern(n.timestamp('2026-07-15T12:00:00Z'))


def test_imported_claim_is_not_provider_verified_gate(usage):
    service,home=usage
    store.record(service.journal,'antigravity',home,snapshot([n.pool('weekly',used=100,models=['*'])]),kind='import')
    assert service.assessment('antigravity',home,'exact')['state']=='EXHAUSTED'
    assert not service.assessment('antigravity',home,'exact')['blockable']
    service.guard('antigravity',home,{'model':'exact'})


def test_dedupe_does_not_renew_freshness_and_scope_changes_invalidate(usage):
    service,home=usage;home.mkdir();payload=snapshot([n.pool('weekly',used=10,models=['*'])])
    first=store.record(service.journal,'claude',home,payload,at=10,dedupe=True)
    second=store.record(service.journal,'claude',home,payload,at=100,dedupe=True)
    assert first==second and store.latest(service.journal,'claude',home)['observed_at']==10
    (home/'settings.json').write_text('{"model":"new-default"}')
    assert service.snapshot('claude',home)['source']=='NONE'


def test_refresh_single_flight_failure_and_no_transaction_during_io(usage):
    service,home=usage
    async def test():
        calls=0
        async def collect(journal,h):
            nonlocal calls
            calls+=1
            assert not journal.db.in_transaction
            await asyncio.sleep(.05)
            return snapshot([n.pool('shared',used=30,models=['*'])])
        with patch('nativepeers.usage.service.COLLECTORS',{'codex':type('Collector',(),{'collect':staticmethod(collect)})()}):
            await asyncio.gather(*(service.refresh('codex',home,force=True) for _ in range(3)))
        assert calls==1
        with patch('nativepeers.usage.service.COLLECTORS',{'codex':type('Collector',(),{'collect':AsyncMock(side_effect=TimeoutError())})()}):
            await service.refresh('codex',home,force=True)
        view=service.snapshot('codex',home)
        assert view['pools'][0]['used']==30 and view['last_refresh']['code']=='SOURCE_TIMEOUT'
    asyncio.run(test())


def test_retention_is_bounded_and_preserves_last_success(usage):
    service,home=usage
    store.record(service.journal,'codex',home,snapshot([n.pool('shared',used=30)]))
    for _ in range(160):store.record(service.journal,'codex',home,{'code':'FAILED'},kind='refresh')
    assert service.journal.one('SELECT COUNT(*) n FROM usage_observations')['n']<=128
    assert store.latest(service.journal,'codex',home,'snapshot')


def test_policy_validation_rejects_unknown_or_invalid_settings():
    for bad in ({'auto_select':True},{'freshness_seconds':0},{'critical_remaining_percent':99},{'pool_concurrency':{'codex:a':9}}):
        with pytest.raises(PeerError):validate(bad)


def test_observed_model_support_does_not_cross_provider_homes(usage):
    service,home=usage
    service.journal.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',('group','parent','{}','COMPLETED','manifest'))
    service.journal.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        ('worker','group',None,'claude','native','title','opus','claude-opus-5-5',None,
         str(home),None,'RELEASED_RESUMABLE',1,json.dumps({'provider_home':str(home.resolve())})))
    assert service.snapshot('claude',home)['models'][1]['support']=='OBSERVED_NATIVE'
    assert not service.snapshot('claude',home/'other')['models']
