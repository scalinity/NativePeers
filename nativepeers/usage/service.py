"""Shared CLI/MCP usage reads, bounded refresh and exact-choice assessment."""
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import time

from ..errors import PeerError,require
from ..files import advisory_lock,canonical,sha256
from ..launch import provider_home
from .collectors import COLLECTORS
from .config import load
from .normalize import PROVIDERS,aggregate,assess_pool
from . import store


def same_model(provider,wanted,actual):
    if not isinstance(wanted,str) or not isinstance(actual,str):return False
    wanted=wanted.removesuffix('[1m]');actual=actual.removesuffix('[1m]')
    return wanted==actual or provider=='claude' and any(alias in ('sonnet','opus','haiku') and full.startswith('claude-'+alias+'-') for alias,full in ((wanted,actual),(actual,wanted)))


@asynccontextmanager
async def refresh_lock(path):
    # Reuse the owned advisory lock without leaving a blocked acquisition thread
    # behind if a caller disconnects while waiting for another collector.
    deadline=time.monotonic()+10
    while True:
        lock=advisory_lock(path,timeout=0)
        try:lock.__enter__();break
        except TimeoutError:
            if time.monotonic()>=deadline:raise
            await asyncio.sleep(.02)
    try:yield
    finally:lock.__exit__(None,None,None)


class Usage:
    def __init__(self,journal):self.journal=journal;self.settings=load(journal)

    def home(self,provider,caller=None,label=None):
        default=provider_home(provider)
        choices={str(default)}
        if caller and caller.kind=='peer':
            from ..registry import Registry
            peer=Registry(self.journal).verify(caller.key,caller.incarnation)
            if peer['peer_key']['provider']==provider:default=Path(peer['home']);choices.add(str(default))
        if not label:return default
        from ..policy import load as policy
        from ..models import home_id
        labels=policy(self.journal)['home_labels']
        for row in self.journal.all('SELECT descriptor_json FROM peer_instances WHERE provider=?',(provider,)):
            choices.add(json.loads(row['descriptor_json'])['home'])
        matches=[Path(h) for h in choices if labels.get(home_id(Path(h)))==label]
        require(len(matches)==1,'AMBIGUOUS_TARGET' if matches else 'TARGET_OFFLINE','Usage home label must identify one known provider home')
        return matches[0]

    def acquisition_fresh(self,observation,now=None):
        if not observation:return False
        now=time.time() if now is None else now
        ttl=self.settings['provider_freshness'].get(observation['provider'],self.settings['freshness_seconds'])
        if not -5<=now-observation['observed_at']<=ttl:return False
        payload=observation['payload']
        if payload.get('collection',{}).get('outcome') not in ('FRESH','UNAVAILABLE'):return False
        return all(assess_pool(p,now,ttl,p.get('observed_at',observation['observed_at']),self.settings)['freshness']=='FRESH'
            for p in payload.get('pools',[]) if p.get('limiting',True) and not p.get('disabled'))

    async def refresh(self,provider,home,*,force=False):
        initial=store.latest(self.journal,provider,home,'snapshot',active=True)
        initial_id=initial['observation_id'] if initial else 0
        if not force and self.acquisition_fresh(initial):return
        scope=store.scope(provider,home);key=sha256([provider,str(home),scope]);started=time.time()
        try:
            async with refresh_lock(self.journal.root/'locks'/('usage-'+key+'.lock')):
                current=store.latest(self.journal,provider,home,'snapshot',active=True)
                if current and current['observation_id']!=initial_id and self.acquisition_fresh(current):return
                try:
                    payload=await COLLECTORS[provider].collect(self.journal,home)
                    require(store.scope(provider,home)==scope,'SOURCE_SCOPE_CHANGED','Native home/runtime/authentication metadata changed during the read; repeat the read in the current scope')
                    require(payload is not None,'SOURCE_UNAVAILABLE','This native usage route is not yet qualified')
                    kind=payload.pop('kind','snapshot');completed=time.time()
                    collection=payload.setdefault('collection',dict(outcome='FRESH',freshness_basis='native-fetch-completed'))
                    collection.update(request_started_at=started,request_completed_at=completed)
                    if kind=='snapshot' and collection['outcome'] in ('FRESH','UNAVAILABLE'):
                        import uuid
                        payload['acquisition_id']=str(uuid.uuid4())
                    store.record(self.journal,provider,home,payload,kind=kind,settings=self.settings)
                except asyncio.CancelledError:raise
                except Exception as error:
                    code=error.code if isinstance(error,PeerError) else 'SOURCE_TIMEOUT' if isinstance(error,TimeoutError) else 'EXECUTABLE_NOT_FOUND' if isinstance(error,FileNotFoundError) else 'REFRESH_FAILED'
                    from ..adapters.codex_app_server import ProviderError
                    if isinstance(error,ProviderError):
                        from ..provider_errors import classify
                        classified=classify(error.error)
                        denied=isinstance(error.error,dict) and any(error.error.get(k) in (401,403) for k in ('status','status_code','http_status'))
                        code='SOURCE_ACCESS_DENIED' if classified=='AUTH_REQUIRED' or denied else 'SOURCE_RATE_LIMITED' if classified=='RATE_LIMITED' else 'REFRESH_FAILED'
                        if isinstance(error.error,dict) and error.error.get('code')==-32601:code='CAPABILITY_MISMATCH'
                    elif code=='AUTH_REQUIRED':code='SOURCE_ACCESS_DENIED'
                    details=error.details if isinstance(error,PeerError) and isinstance(error.details,dict) else {}
                    store.record(self.journal,provider,home,dict(code=code,message='Current quota could not be obtained: '+code,
                        request_started_at=started,request_completed_at=time.time(),
                        **{k:details[k] for k in ('retry_at','retry_after_seconds','retry_note') if k in details}),kind='refresh',settings=self.settings)
        except TimeoutError:
            store.record(self.journal,provider,home,dict(code='SOURCE_BUSY',message='Another native quota collector still owns this scope; retained measurements keep their age.',
                request_started_at=started,request_completed_at=time.time()),kind='refresh',settings=self.settings)

    def snapshot(self,provider,home,*,model=None,now=None):
        now=time.time() if now is None else now
        ttl=self.settings['provider_freshness'].get(provider,self.settings['freshness_seconds'])
        native=store.latest(self.journal,provider,home,'snapshot')
        imported=store.latest(self.journal,provider,home,'import')
        selected=native or imported
        payload=selected['payload'] if selected else {}
        catalog=store.latest(self.journal,provider,home,'catalog')
        pools=[]
        for p in payload.get('pools',[]):
            p=dict(p)
            if selected['kind']=='import':p['basis']='user-supplied'
            # An exact model key is usable; a family/bucket name is not guessed.
            if provider=='antigravity' and p['pool_id'] in {m['model'] for m in payload.get('models',[])}:
                p.update(models=[p['pool_id']],membership='exact-native-model-key')
            pools.append(assess_pool(p,now,ttl,p.get('observed_at',selected['observed_at']),self.settings))
        errors=self.journal.all("SELECT observation_id,observed_at,payload_json FROM usage_observations WHERE provider=? AND home=? AND scope=? AND kind='native-error' ORDER BY observation_id DESC LIMIT 64",(provider,str(Path(home).resolve()),store.scope(provider,home)))
        seen=set()
        for row in errors:
            for p in json.loads(row['payload_json']).get('pools',[]):
                if p['pool_id'] in seen:continue
                seen.add(p['pool_id'])
                scope=p.get('limiting_scope')
                if scope and selected and selected['observed_at']>row['observed_at'] and any(
                    q['pool_id']==scope and q['freshness']=='FRESH' and q['state'] in ('AVAILABLE','LIMITED','EXHAUSTED') for q in pools):continue
                pools.append(assess_pool(p,now,ttl,row['observed_at'],self.settings))
        for mapping in self.settings['pool_memberships']:
            if mapping['provider']!=provider:continue
            for p in pools:
                if p['pool_id']==mapping['pool_id']:
                    p['models']=sorted(set(p['models']+[mapping['model']]))
                    p['membership']='explicit-local-policy'
        from .forecast import runway
        for p in pools:
            p['runway']=runway(self.journal,provider,home,store.scope(provider,home),p,now,ttl)
        models={m['model']:dict(m) for m in [*payload.get('models',[]),*(catalog['payload'].get('models',[]) if catalog else [])][:64]}
        for m in self.settings['models']:
            if m['provider']==provider:models.setdefault(m['model'],dict(model=m['model'],capability_source=m['source'],support='LOCAL_PREFERENCE_ONLY',entitlement='UNVERIFIED'))
        for row in self.journal.all('SELECT model_requested,model_observed,lease_json FROM workers WHERE provider=? ORDER BY rowid DESC LIMIT 128',(provider,)):
            if json.loads(row['lease_json']).get('provider_home')!=str(Path(home).resolve()):continue
            for name in (row['model_requested'],row['model_observed']):
                if name:models.setdefault(name,dict(model=name,capability_source='retained-native-worker',support='OBSERVED_NATIVE' if name==row['model_observed'] else 'REQUESTED_ONLY',entitlement='UNVERIFIED'))
        if model:models.setdefault(model,dict(model=model,capability_source='requested',entitlement='UNVERIFIED'))
        model_rows=[]
        for name,metadata in list(models.items())[:64]:
            if model and not same_model(provider,model,name):continue
            applicable=[p for p in pools if p.get('limiting',True) and ('*' in p['models'] or any(same_model(provider,name,m) for m in p['models']))]
            state=aggregate(applicable)
            if state=='AVAILABLE' and any(p.get('limiting',True) and not p['models'] for p in pools):state='UNKNOWN'
            model_rows.append(dict(metadata,state=state,pool_ids=[p['pool_id'] for p in applicable],
                limiting_pools=[p['pool_id'] for p in applicable if p['state']!='AVAILABLE'],
                coverage=payload.get('coverage','unknown'),
                reasons=['Only known applicable constraints are evaluated; catalog presence is not entitlement.']+
                    (['Model-to-pool membership is unavailable.'] if not applicable else [])))
        refresh=store.latest(self.journal,provider,home,'refresh')
        source_failure=refresh['payload']['code'] if refresh and now-refresh['observed_at']<=ttl else None
        if source_failure and selected and refresh['observed_at']<selected['observed_at']:source_failure=None
        return dict(provider=provider,home=str(Path(home).resolve()),scope=store.scope(provider,home),
            state=aggregate([p for p in pools if p.get('limiting',True)]),collector_state=source_failure or (payload.get('collection',{}).get('outcome','OK') if selected else 'UNKNOWN'),
            route=payload.get('route','native-auth-unknown'),source=payload.get('source','NONE'),
            source_version=payload.get('source_version'),collection=payload.get('collection'),observation_id=selected['observation_id'] if selected else None,
            observed_at=selected['observed_at'] if selected else None,
            age_seconds=max(0,now-selected['observed_at']) if selected else None,
            freshness_seconds=ttl,confidence='IMPORTED' if selected and not native else 'NATIVE_OBSERVATION' if selected else 'UNKNOWN',
            coverage=payload.get('coverage','unknown'),pools=pools,models=model_rows,
            unmapped_pool_ids=[p['pool_id'] for p in pools if not p['models']],
            last_refresh=refresh['payload'] if refresh else None,
            limitations=payload.get('limitations',[])+['NativePeers does not observe all account activity.']+
                (['Authentication changes hidden in a native credential store cannot be independently detected from status-line metadata.'] if provider!='codex' else []))

    async def status(self,caller=None,*,provider=None,model=None,home_label=None,refresh=False,requirements=None,preference='balanced'):
        require(provider is None or provider in PROVIDERS,'PROTOCOL_ERROR','Unknown usage provider')
        require(model is None or isinstance(model,str) and 0<len(model)<=512 and all(ord(c)>=32 for c in model),'PROTOCOL_ERROR','Invalid exact usage model')
        require(not home_label or provider,'PROTOCOL_ERROR','A home label requires a provider')
        providers=[provider] if provider else list(PROVIDERS)
        homes={p:self.home(p,caller,home_label) for p in providers}
        if refresh:await asyncio.gather(*(self.refresh(p,h,force=True) for p,h in homes.items()))
        result=dict(schema_version=1,providers=[self.snapshot(p,h,model=model) for p,h in homes.items()],
            inference_submitted=False,quota_reserved=False)
        from .forecast import local_summary,recommend
        result['local_usage']=local_summary(self.journal)
        result['show_local_estimates']=self.settings['show_local_estimates']
        if requirements is not None:result['recommendations']=recommend(result,requirements,preference,self.settings)
        return result

    def assessment(self,provider,home,model,effort=None):
        view=self.snapshot(provider,home,model=model)
        selected=next((m for m in view['models'] if same_model(provider,model,m['model'])),None)
        ids=selected['pool_ids'] if selected else []
        pools=[p for p in view['pools'] if p['pool_id'] in ids]
        return dict(provider=provider,home=view['home'],scope=view['scope'],model=model,reasoning_effort=effort,
            state=selected['state'] if selected else 'UNKNOWN',pools=pools,source=view['source'],
            confidence=view['confidence'],age_seconds=view['age_seconds'],coverage=view['coverage'],observation_id=view['observation_id'],
            blockable=any(p['state']=='EXHAUSTED' and p.get('basis')=='provider-reported' for p in pools),
            limitations=view['limitations'])

    def guard(self,provider,home,spec):
        assessment=self.assessment(provider,home,spec['model'],spec.get('reasoning_effort'))
        if spec.get('quota_policy','respect-known-limits')!='warn' and assessment['blockable']:
            exhausted=[p for p in assessment['pools'] if p['state']=='EXHAUSTED']
            raise PeerError('QUOTA_EXHAUSTED','Known native quota blocks '+spec['model']+': '+', '.join(p['pool_id'] for p in exhausted),
                details=dict(usage=assessment,execution={'input_submitted':False,'definitive_no_execution':True}))
        return assessment

    def batch(self,workers):
        assessments=[];demand={}
        homes={p:provider_home(p) for p in PROVIDERS}
        for worker in workers:homes[worker['lease']['spec']['provider']]=worker['lease']['provider_home']
        views={'providers':[self.snapshot(p,h) for p,h in homes.items()]}
        from .forecast import estimate,recommend
        for worker in workers:
            lease=worker['lease'];spec=lease['spec']
            item=self.assessment(spec['provider'],lease['provider_home'],spec['model'],spec.get('reasoning_effort'))
            item['title']=spec['title'];assessments.append(item)
            item['task_estimate']=estimate(self.journal,item['provider'],item['home'],item['model'],item['reasoning_effort'],spec.get('requirements',{}),spec.get('permission_profile','analysis'))
            item['recommendations']=recommend(views,spec['requirements'],'balanced',self.settings) if spec.get('requirements') else None
            item['remaining_after_batch']='UNKNOWN: no validated subscription quota-per-task conversion.'
            for p in item['pools']:
                key=sha256([item['provider'],item['home'],item['scope'],p['pool_id']])
                d=demand.setdefault(key,dict(provider=item['provider'],home=item['home'],pool_id=p['pool_id'],proposed_workers=0,
                    active_workers=0,ceiling=self.settings['pool_concurrency'].get(item['provider']+':'+p['pool_id'])))
                d['proposed_workers']+=1
        for row in self.journal.all("SELECT lease_json FROM workers w WHERE state NOT IN ('RELEASED_RESUMABLE','FAILED','EXITED') AND EXISTS (SELECT 1 FROM tasks t WHERE t.worker_id=w.worker_id AND t.generation=w.generation AND t.state IN ('QUEUED','RUNNING','WAITING','APPROVAL_BLOCKED','FALLBACK_PENDING'))"):
            for key in json.loads(row['lease_json']).get('usage_demand',[]):
                if key in demand:demand[key]['active_workers']+=1
        return dict(workers=assessments,demands=[dict(v,demand_key=k) for k,v in demand.items()],shared_pools=[dict(v,demand_key=k) for k,v in demand.items() if v['proposed_workers']+v['active_workers']>1 or v['ceiling']],
            warnings=['Local demand is advisory and cannot reserve vendor quota.'],
            inference_submitted=False)

    def admit_local_demand(self,lease,*,worker_id=None):
        """Call inside the existing admission transaction, before reserving new work."""
        if not self.settings['pool_concurrency']:return None
        plan=self.batch([{'lease':lease}]);old_keys=lease.get('usage_demand',[])
        existing=self.journal.one("SELECT 1 active FROM tasks t JOIN workers w USING(worker_id) WHERE w.worker_id=? AND t.generation=w.generation AND t.state IN ('QUEUED','RUNNING','WAITING','APPROVAL_BLOCKED','FALLBACK_PENDING') LIMIT 1",(worker_id,)) if worker_id else None
        for demand in plan['demands']:
            own=bool(existing and demand['demand_key'] in old_keys)
            require(demand['ceiling'] is None or demand['active_workers']+1-int(own)<=demand['ceiling'],
                'CAPACITY_UNAVAILABLE','Configured local quota-pool concurrency ceiling reached',details=demand)
        lease['usage_demand']=[d['demand_key'] for d in plan['demands']]
        return plan['workers'][0]


def format_summary(value,details=False):
    def safe(text):return ''.join(c for c in str(text) if c.isprintable())
    lines=[]
    for provider in value['providers']:
        age='no observation' if provider['age_seconds'] is None else f"{int(provider['age_seconds'])}s old"
        lines.append(f"{provider['provider']}: {provider['state']} — {safe(provider['source'])}, {age}")
        failure=provider.get('collector_state')
        if failure not in ('OK','FRESH','CACHED',None):
            meaning={'SOURCE_ACCESS_DENIED':'Usage read denied; native sign-in status is unconfirmed.',
                'SOURCE_RATE_LIMITED':'Usage refresh is temporarily limited; this does not establish model quota exhaustion.',
                'SOURCE_NOT_QUALIFIED':'This native usage transport needs its scoped verification.',
                'EXECUTABLE_NOT_FOUND':'The configured native executable is missing.',
                'UNVERIFIED':'Numeric data is retained; current backend freshness is unverified.'}.get(failure,'A current quota report could not be obtained; retained measurements keep their age.')
            lines.append('  '+safe(failure)+': '+meaning)
        for p in provider['pools']:
            capacity='capacity unknown' if p['remaining'] is None else f"{p['remaining']:g} {p['unit']} remaining"
            reset='; resets '+p['reset_eastern'] if p['reset_eastern'] else ''
            if not reset and p.get('reset_note'):reset='; provider reset text: '+safe(p['reset_note'])+' (date unconfirmed)'
            if p['seconds_until_reset'] is not None:reset+=f" (in {int(p['seconds_until_reset']//60)}m)"
            used=f"; {p['used']:g} {p['unit']} used" if p['used'] is not None else ''
            warnings='; '+', '.join(p['warnings']) if p['warnings'] else ''
            lines.append(f"  {safe(p['pool_id'])}: {p['state']}; {capacity}{used}{reset}{warnings}")
            if p.get('disabled'):lines.append('    Disabled native bucket; not a limiting pool.')
            if details and p.get('runway',{}).get('state')=='ESTIMATE':
                lines.append(f"    Estimated runway at recent account pace: {int(p['runway']['seconds_at_recent_pace']//60)}m; includes activity outside NativePeers.")
        for m in provider['models']:
            lines.append(f"  {safe(m['model'])}: {m['state']}"+(' — '+', '.join(map(safe,m['limiting_pools'])) if m['limiting_pools'] else ''))
        if provider['unmapped_pool_ids']:lines.append('  Some quota pools have no confirmed model membership.')
        if details:lines.extend('  '+safe(x) for x in provider['limitations'])
    recommendations=value.get('recommendations')
    if recommendations:
        lines.append('Recommendations ('+recommendations['preference']+'):')
        if not recommendations['candidates']:lines.append('  No candidate has enough attributed capability evidence for these requirements; configure usage.models with sources.')
        for candidate in recommendations['candidates'][:3]:
            lines.append('  '+safe(candidate['provider']+':'+candidate['model'])+' — '+' '.join(map(safe,candidate['reasons'])))
    if details or value.get('show_local_estimates'):
        local=value.get('local_usage',{})
        lines.append('Owned task history: '+str(local.get('bounded_attempts',0))+' retained measured/unmeasured attempts; account activity elsewhere is excluded.')
        for item in local.get('models',[])[:12]:
            lines.append(f"  {safe(item['provider'])}:{safe(item['model'])}: {item['attempts']} attempts, {item['measured_attempts']} with token metrics; {item['input_tokens']:g} input / {item['output_tokens']:g} output tokens; {item['quota_errors']} quota/rate errors")
    lines.append('Account quota and NativePeers-owned task consumption are separate; no inference was submitted.')
    return '\n'.join(lines)
