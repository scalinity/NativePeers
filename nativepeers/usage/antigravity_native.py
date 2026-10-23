"""The vendor's local read-only print commands, never model prompt execution."""
import json

from ..errors import PeerError,require
from .normalize import number,pool,timestamp


def envelope(raw,command='usage'):
    try:value=json.loads(raw)
    except (ValueError,TypeError):raise PeerError('SOURCE_SCHEMA_MISMATCH','Antigravity returned no structured command report') from None
    require(isinstance(value,dict),'SOURCE_SCHEMA_MISMATCH','Expected native command report object')
    if value.get('status')!='SUCCESS':
        from ..provider_errors import classify
        code=classify(value.get('error') or value.get('response') or {})
        raise PeerError('SOURCE_ACCESS_DENIED' if code=='AUTH_REQUIRED' else 'SOURCE_RATE_LIMITED' if code=='RATE_LIMITED' else 'SOURCE_UNAVAILABLE',
            'Antigravity native usage command could not obtain a current report')
    require(value.get('conversation_id')=='' and type(value.get('num_turns')) is int and value['num_turns']==0,
        'SOURCE_EXECUTION_UNCONFIRMED','Native report does not prove zero turns and no conversation')
    usage=value.get('usage')
    require(isinstance(usage,dict) and all(type(usage.get(k)) in (int,float) and usage[k]==0 for k in ('input_tokens','output_tokens'))
        and all(type(v) in (int,float) and v==0 for k,v in usage.items() if k.endswith('tokens')),
        'SOURCE_EXECUTION_UNCONFIRMED','Native report lacks zero invocation token counters')
    body=value.get('command')
    require(isinstance(body,dict) and body.get('name')==command and isinstance(body.get('data'),dict),
        'SOURCE_SCHEMA_MISMATCH','Native report command identity/schema differs')
    return value,body['data']


def parse(raw,*,fresh_verified=False):
    value,data=envelope(raw);groups=data.get('groups')
    require(isinstance(groups,list) and 0<len(groups)<=64,'SOURCE_SCHEMA_MISMATCH','Expected bounded native quota groups')
    pools=[];seen=set()
    for group in groups:
        require(isinstance(group,dict) and isinstance(group.get('buckets'),list),'SOURCE_SCHEMA_MISMATCH','Invalid native quota group')
        for bucket in group['buckets']:
            require(isinstance(bucket,dict) and len(pools)<256,'SOURCE_SCHEMA_MISMATCH','Invalid/oversized native quota buckets')
            identifier=bucket.get('bucket_id') or bucket.get('bucketId') or bucket.get('id')
            require(isinstance(identifier,str) and 0<len(identifier)<=256 and identifier not in seen,'SOURCE_SCHEMA_MISMATCH','Native bucket IDs must be bounded and unique')
            seen.add(identifier);nested=bucket.get('remaining') or {}
            require(isinstance(nested,dict),'SOURCE_SCHEMA_MISMATCH','Invalid native remaining-value object')
            fraction=bucket.get('remaining_fraction',bucket.get('remainingFraction',nested.get('remaining_fraction',nested.get('remainingFraction'))))
            if fraction is None and nested.get('case')=='remainingFraction':fraction=nested.get('value')
            amount=bucket.get('remaining_amount',nested.get('remaining_amount'))
            if amount is None and nested.get('case')=='remainingAmount':amount=nested.get('value')
            require(fraction is None or number(fraction,maximum=1) is not None,'SOURCE_SCHEMA_MISMATCH','Invalid native quota fraction')
            require(amount is None or number(amount) is not None,'SOURCE_SCHEMA_MISMATCH','Invalid native quota amount')
            require(type(bucket.get('disabled',False)) is bool,'SOURCE_SCHEMA_MISMATCH','Invalid native disabled flag')
            window=bucket.get('window');disabled=bucket.get('disabled',False)
            models=bucket.get('models',[])
            require(isinstance(models,list) and len(models)<=64 and all(isinstance(m,str) for m in models),'SOURCE_SCHEMA_MISMATCH','Invalid explicit model membership')
            item=pool(identifier,remaining=fraction*100 if fraction is not None else amount,
                unit='percent' if fraction is not None or amount is None else 'native-amount',
                reset=bucket.get('reset_time',bucket.get('resetTime')),duration={'weekly':604800,'5h':18000,'five_hour':18000}.get(window),
                models=models,membership='native-explicit-models' if models else 'unknown',
                kind='shared',disabled=disabled,limiting=not disabled,window_name=window,
                group=str(group.get('name',group.get('display_name','')))[:256],label=str(bucket.get('name',bucket.get('display_name',identifier)))[:256])
            item['field_basis']['remaining']='derived-from-reported-fraction' if fraction is not None else 'provider-reported' if amount is not None else 'unknown'
            if not fresh_verified:item['observed_at']=0
            pools.append(item)
    require(pools,'SOURCE_SCHEMA_MISMATCH','Native report has no quota buckets')
    return dict(source='antigravity-native-usage',route='native-google',pools=pools,models=[],coverage='partial',
        collection=dict(outcome='FRESH' if fresh_verified else 'UNVERIFIED',freshness_basis='inspected-native-force-refresh' if fresh_verified else 'native-fetch-unverified',provider_observed_at=timestamp(data.get('observed_at')),
            command='usage',num_turns=0,conversation_created=False,credits_coverage='UNKNOWN'),
        limitations=['All returned groups retained; display names do not establish exact model membership.',
            'Credits are separate and remain unknown until the native credits schema is qualified.'])


def credits(raw):
    """Installed 1.4.5 print schema; credits never clear an included-usage limit."""
    _,data=envelope(raw,'credits')
    remaining=data.get('remaining_credits')
    require(number(remaining) is not None,'SOURCE_SCHEMA_MISMATCH','Native credits report lacks a numeric credit balance')
    return pool('additional-credits',remaining=remaining,unit='credits',kind='credits',
        limiting=False,membership='unknown',resolution=1 if type(remaining) is int else None)
