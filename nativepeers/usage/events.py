"""Ordinary owned-provider telemetry, without a second native event reader."""
import time

from . import store
from .normalize import codex


def codex_update(journal,home,params,runtime):
    incoming=params.get('rateLimits')
    if not isinstance(incoming,dict):return
    previous=store.latest(journal,'codex',home,'snapshot')
    payload=dict(previous['payload']) if previous else dict(source='codex-notification',source_version=runtime,
        route='subscription',coverage='partial',models=[],pools=[],limitations=['Sparse native update; absent fields are unavailable.'])
    old={p['pool_id']:dict(p,observed_at=p.get('observed_at',previous['observed_at'])) for p in payload['pools']} if previous else {}
    for p in codex({'rateLimits':incoming}):
        if p['used'] is None and p['remaining'] is None and p.get('state') is None:continue
        identifier=p['pool_id'];merged=dict(old.get(identifier,{}))
        merged.update({k:v for k,v in p.items() if v is not None});merged['observed_at']=time.time();old[identifier]=merged
    payload['pools']=list(old.values())[:128]
    store.record(journal,'codex',home,payload)


def codex_tokens(params):
    usage=params.get('tokenUsage',{}).get('last',{})
    return {target:usage[source] for source,target in [('inputTokens','input_tokens'),('outputTokens','output_tokens'),
        ('cachedInputTokens','cache_read_input_tokens'),('reasoningOutputTokens','reasoning_tokens'),('totalTokens','total_tokens')] if source in usage}
