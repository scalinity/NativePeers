"""Bounded public quota values. Account identity and raw diagnostics never escape."""
from datetime import datetime
import math
from zoneinfo import ZoneInfo
from ..errors import require

PROVIDERS = ('claude', 'codex', 'antigravity')


def number(value, minimum=0, maximum=None):
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
        return None
    return value if maximum is None or value <= maximum else None


def timestamp(value):
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return parsed.timestamp() if parsed.tzinfo else None
        except (ValueError, OverflowError):
            return None
    return number(value)


def eastern(value):
    try:
        return datetime.fromtimestamp(value, ZoneInfo('America/New_York')).strftime('%Y-%m-%d %-I:%M %p %Z')
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def pool(identifier, *, used=None, remaining=None, total=None, unit='percent',
         reset=None, duration=None, models=(), kind='other', resolution=None,
         state=None, membership='unknown', percent_overage=False, **extra):
    supplied_used,supplied_remaining=used,remaining
    used, remaining, total = number(used), number(remaining), number(total)
    if unit == 'percent':
        used, remaining = number(used, maximum=None if percent_overage else 100), number(remaining, maximum=100)
        if remaining is None and used is not None:
            remaining = max(0,100 - used)
        if used is None and remaining is not None:
            used = 100 - remaining
    return dict(pool_id=str(identifier)[:256], kind=kind, used=used, remaining=remaining,
                total=total, unit=unit, resets_at=timestamp(reset), window_seconds=number(duration),window_type='provider-window' if duration is not None else 'unknown',
                models=list(models)[:64], membership=membership, resolution=resolution,
                state=state, basis='provider-reported',
                field_basis=dict(used='provider-reported' if supplied_used is not None else 'derived-complement' if used is not None else 'unknown',
                    remaining='provider-reported' if supplied_remaining is not None else 'derived-complement' if remaining is not None else 'unknown',
                    reset='provider-supplied' if timestamp(reset) is not None else 'unknown',countdown='derived-from-reset'),**extra)


def assess_pool(value, now, freshness, observed_at, settings):
    result=dict(value)
    age=now-observed_at
    reset=value.get('resets_at')
    stale=age < -5 or age > freshness or (reset is not None and now >= reset)
    remaining=value.get('remaining')
    state=value.get('state')
    if value.get('disabled') is True:
        state='UNKNOWN';result['limiting']=False
    elif stale:
        state='STALE'
    elif state not in ('AVAILABLE','EXHAUSTED','AUTH_REQUIRED','MODEL_UNAVAILABLE','CAPABILITY_MISMATCH','LIMITED'):
        state='EXHAUSTED' if remaining == 0 else 'UNKNOWN' if remaining is None else 'AVAILABLE'
        if state=='AVAILABLE' and value.get('unit')=='percent' and remaining <= settings['low_remaining_percent']:
            state='LIMITED'
    result.update(state=state, observed_at=observed_at, age_seconds=max(0, age), freshness='STALE' if stale else 'FRESH',
                  reset_eastern=eastern(reset), seconds_until_reset=max(0,reset-now) if reset is not None else None)
    warnings=[]
    if not stale and result.get('limiting',True) and value.get('unit')=='percent' and remaining is not None:
        if remaining<=settings['critical_remaining_percent']:warnings.append('CRITICAL_REMAINING')
        elif remaining<=settings['low_remaining_percent']:warnings.append('LOW_REMAINING')
    if not stale and result.get('limiting',True) and reset is not None and 0<reset-now<=settings['reset_warning_seconds']:warnings.append('RESET_SOON')
    result['warnings']=warnings
    return result


def aggregate(pools):
    # One proven applicable failure wins; a missing reading never means full.
    states={p['state'] for p in pools}
    for state in ('AUTH_REQUIRED','CAPABILITY_MISMATCH','MODEL_UNAVAILABLE','EXHAUSTED','LIMITED','STALE','UNKNOWN'):
        if state in states:
            return state
    return 'AVAILABLE' if pools else 'UNKNOWN'


def codex(value):
    if not isinstance(value,dict):return []
    pools=[]
    buckets=value.get('rateLimitsByLimitId')
    if not isinstance(buckets,dict):
        single=value.get('rateLimits')
        buckets={single.get('limitId') or 'ordinary':single} if isinstance(single,dict) else {}
    require(len(buckets)<=64,'CAPABILITY_MISMATCH','Native quota response exceeds the bounded bucket contract')
    for key,bucket in list(buckets.items())[:64]:
        if not isinstance(bucket,dict):continue
        identifier=bucket.get('limitId') or key
        # Bucket labels and normalModelSlug do not establish full model membership.
        for window in ('primary','secondary'):
            w=bucket.get(window)
            if not isinstance(w,dict):continue
            pools.append(pool(f'{identifier}/{window}',used=w.get('usedPercent'),reset=w.get('resetsAt'),
                duration=w.get('windowDurationMins')*60 if number(w.get('windowDurationMins')) is not None else None,
                kind='shared',resolution=1,bucket_id=identifier))
        credits=bucket.get('credits')
        if isinstance(credits,dict):
            try:balance=float(credits.get('balance'))
            except (TypeError,ValueError):balance=None
            pools.append(pool(f'{identifier}/credits',remaining=balance,unit='credits',kind='credits',
                state='AVAILABLE' if credits.get('unlimited') is True else None,
                unlimited=credits.get('unlimited') is True, has_credits=credits.get('hasCredits'),
                limiting=False))
        individual=bucket.get('individualLimit')
        if isinstance(individual,dict):
            pools.append(pool(f'{identifier}/individual',remaining=individual.get('remainingPercent'),
                reset=individual.get('resetsAt'),kind='shared',resolution=1,bucket_id=identifier,
                reported_spend_used=str(individual.get('used',''))[:64],reported_spend_limit=str(individual.get('limit',''))[:64],reported_spend_unit='unknown'))
        reached=bucket.get('rateLimitReachedType')
        if reached or bucket.get('spendControlReached') is True:
            pools.append(pool(f'{identifier}/refusal',state='EXHAUSTED',kind='shared',bucket_id=identifier,reason_code=str(reached or 'spend_control_reached')[:128]))
    permission=value.get('ordinaryUsageAllowed')
    if type(permission) is bool:
        pools.append(pool('ordinary-usage-permission',state='AVAILABLE' if permission else 'EXHAUSTED',
            models=['*'],kind='provider-wide',membership='native-account-permission'))
        pools[-1]['state']='AVAILABLE' if permission else 'EXHAUSTED'
        pools[-1]['permission']=permission
    resets=value.get('rateLimitResetCredits')
    if isinstance(resets,dict):
        pools.append(pool('reset-credits',remaining=resets.get('availableCount'),unit='credits',kind='credits',limiting=False))
    return pools


def claude(value):
    if not isinstance(value,dict):return []
    rates=value.get('rate_limits')
    if not isinstance(rates,dict):return []
    require(len(rates)<=64,'CAPABILITY_MISMATCH','Native quota response exceeds the bounded window contract')
    pools=[]
    windows={'five_hour':18000,'seven_day':604800,'spend_limit':None}
    for name in list(rates)[:64]:
        seconds=windows.get(name)
        item=rates.get(name)
        if not isinstance(item,dict):continue
        if 'used_percentage' not in item:continue
        pools.append(pool(name,used=item.get('used_percentage'),reset=item.get('resets_at'),duration=seconds,percent_overage=name=='spend_limit',
            kind='provider-wide' if name in windows else 'other',models=['*'] if name in windows else [],
            membership='documented-native-window' if name in windows else 'unknown'))
        # Dollar-denominated gateway spend is a separate actual unit.
        if name=='spend_limit' and number(item.get('used_usd')) is not None:
            total=number(item.get('limit_usd'));used=number(item['used_usd'])
            pools.append(pool('spend_limit/usd',used=used,total=total,remaining=max(0,total-used) if total is not None else None,
                unit='USD',reset=item.get('resets_at'),kind='provider-wide',models=['*'],membership='documented-native-window'))
    return pools


def antigravity(value):
    if not isinstance(value,dict):return []
    quotas=value.get('quota')
    if not isinstance(quotas,dict):return []
    require(len(quotas)<=64,'CAPABILITY_MISMATCH','Native quota response exceeds the bounded pool contract')
    pools=[]
    for identifier,item in list(quotas.items())[:64]:
        if not isinstance(item,dict):continue
        remaining=number(item.get('remaining_fraction'),maximum=1);amount=number(item.get('remaining_amount'))
        pools.append(pool(identifier,remaining=remaining*100 if remaining is not None else amount,
            unit='percent' if remaining is not None or amount is None else 'native-amount',
            reset=item.get('reset_time'),kind='other',disabled=item.get('disabled') is True,limiting=item.get('disabled') is not True,
            fraction_reported=remaining,reset_in_seconds_reported=number(item.get('reset_in_seconds'))))
        pools[-1]['field_basis']['remaining']='derived-from-reported-fraction' if remaining is not None else 'provider-reported' if amount is not None else 'unknown'
    return pools


PARSERS={'codex':codex,'claude':claude,'antigravity':antigravity}
