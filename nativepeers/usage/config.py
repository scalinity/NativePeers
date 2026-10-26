"""Small validated usage preferences inside the existing local policy."""
from ..errors import require

DEFAULTS=dict(freshness_seconds=120,provider_freshness={},retention_days=30,observations_per_scope=128,
    low_remaining_percent=20,critical_remaining_percent=5,reset_warning_seconds=900,
    show_local_estimates=False,models=[],pool_memberships=[],pool_concurrency={})


def validate(value):
    require(isinstance(value,dict) and not set(value)-set(DEFAULTS),'PROTOCOL_ERROR','Unknown usage policy fields')
    result=dict(DEFAULTS,**value)
    for key,ceiling in [('freshness_seconds',3600),('retention_days',90),('observations_per_scope',512),
                        ('low_remaining_percent',100),('critical_remaining_percent',100),('reset_warning_seconds',86400)]:
        require(type(result[key]) is int and 0<result[key]<=ceiling,'PROTOCOL_ERROR','Invalid usage setting: '+key)
    require(result['critical_remaining_percent']<=result['low_remaining_percent'],'PROTOCOL_ERROR','Critical threshold exceeds low-capacity threshold')
    require(type(result['show_local_estimates']) is bool,'PROTOCOL_ERROR','Invalid local estimate setting')
    require(isinstance(result['provider_freshness'],dict) and all(p in ('claude','codex','antigravity','grok') and type(n) is int and 1<=n<=3600 for p,n in result['provider_freshness'].items()),'PROTOCOL_ERROR','Invalid provider freshness')
    require(isinstance(result['models'],list) and len(result['models'])<=64 and all(isinstance(m,dict) and m.get('provider') in ('claude','codex','antigravity','grok') and isinstance(m.get('model'),str) and isinstance(m.get('source'),str) for m in result['models']),'PROTOCOL_ERROR','Model preferences require provider, model and source')
    allowed={'provider','model','source','quality','context_tokens','tools','modalities','efforts','vendor','priority','efficiency','min_complexity'}
    tasks={'implementation','architecture','debugging','research','review','documentation','transformation'}
    tiers={'routine','standard','demanding'}
    for model in result['models']:
        require(not set(model)-allowed and 0<len(model['model'])<=512 and 0<len(model['source'])<=512,'PROTOCOL_ERROR','Invalid model preference fields')
        require(isinstance(model.get('quality',{}),dict) and all(k in tasks and v in tiers for k,v in model.get('quality',{}).items()),'PROTOCOL_ERROR','Invalid attributed quality preferences')
        for key in ('priority','efficiency'):
            require(type(model.get(key,0)) is int and -100<=model.get(key,0)<=100,'PROTOCOL_ERROR','Invalid recommendation weight')
        require(type(model.get('context_tokens',0)) is int and 0<=model.get('context_tokens',0)<=100000000,'PROTOCOL_ERROR','Invalid context capacity')
        for key in ('tools','modalities','efforts'):
            require(isinstance(model.get(key,[]),list) and len(model.get(key,[]))<=32 and all(isinstance(x,str) and len(x)<=128 for x in model.get(key,[])),'PROTOCOL_ERROR','Invalid model capability list')
        require(model.get('min_complexity','routine') in tiers and isinstance(model.get('vendor',''),str),'PROTOCOL_ERROR','Invalid model reservation or vendor')
    require(isinstance(result['pool_memberships'],list) and len(result['pool_memberships'])<=128 and all(isinstance(m,dict) and m.get('provider') in ('claude','codex','antigravity','grok') and isinstance(m.get('model'),str) and isinstance(m.get('pool_id'),str) for m in result['pool_memberships']),'PROTOCOL_ERROR','Invalid configured pool memberships')
    require(all(not set(m)-{'provider','model','pool_id'} and 0<len(m['model'])<=512 and 0<len(m['pool_id'])<=256 for m in result['pool_memberships']),'PROTOCOL_ERROR','Invalid bounded pool membership')
    require(isinstance(result['pool_concurrency'],dict) and len(result['pool_concurrency'])<=64 and all(isinstance(k,str) and type(v) is int and 1<=v<=8 for k,v in result['pool_concurrency'].items()),'PROTOCOL_ERROR','Invalid explicit local pool ceilings')
    return result


def load(journal):
    from ..policy import load as policy
    return policy(journal)['usage']
