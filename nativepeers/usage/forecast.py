"""Small explainable estimates; local task metrics never become account quota."""
import json
import statistics

from ..errors import require
from ..files import canonical
from .normalize import number

TOKEN_FIELDS=('input_tokens','output_tokens','cache_read_input_tokens','cache_creation_input_tokens','reasoning_tokens','total_tokens')
PREFERENCES=('quality-first','balanced','quota-preserving','fast','exact-model-only')


def metrics(value):
    if not isinstance(value,dict):return {}
    return {k:number(value[k]) for k in TOKEN_FIELDS if number(value.get(k)) is not None}


def record_task(journal,row,spec,record,attempt_index,home,before=None):
    from .store import latest
    after=latest(journal,row['provider'],home,'snapshot')
    usage=record.get('usage') if isinstance(record.get('usage'),dict) else {}
    payload=dict(tokens=metrics(usage),cost_usd=number(usage.get('cost_usd')),
        status=record['status'],error_code=(record.get('error') or {}).get('code'),
        native_turns=len(set(record.get('native_turn_ids',[]))) or None,native_turn_ids=record.get('native_turn_ids',[]),
        profile=spec.get('permission_profile'),requirements=spec.get('requirements',{}),
        before_observation_id=before,after_observation_id=after['observation_id'] if after else None,
        attribution='NativePeers-owned attempt only',
        effort_observed=False)
    journal.db.execute('INSERT OR IGNORE INTO task_usage VALUES(?,?,?,?,?,?,?,?,?,?)',
        (record['task_id'],attempt_index,row['worker_id'],row['provider'],str(home),
         record.get('model_observed') or record['model_requested'],spec.get('reasoning_effort'),
         record['started_at'],record['completed_at'],canonical(payload).decode()))


def local_summary(journal):
    rows=journal.all('SELECT provider,model,effort,started_at,completed_at,payload_json FROM task_usage ORDER BY completed_at DESC LIMIT 512')
    grouped={}
    for row in rows:
        p=json.loads(row['payload_json']);key=(row['provider'],row['model'],row['effort'])
        g=grouped.setdefault(key,dict(provider=key[0],model=key[1],reasoning_effort=key[2],attempts=0,
            input_tokens=0,output_tokens=0,measured_attempts=0,seconds=0,quota_errors=0))
        g['attempts']+=1;g['seconds']+=max(0,row['completed_at']-row['started_at'])
        if p.get('tokens'):
            g['measured_attempts']+=1
            for k in ('input_tokens','output_tokens'):g[k]+=p['tokens'].get(k,0)
        g['quota_errors']+=p.get('error_code') in ('QUOTA_EXHAUSTED','RATE_LIMITED')
    return dict(scope='NativePeers-owned attempts only; not an account-wide ledger',
        bounded_attempts=len(rows),models=list(grouped.values()),
        historical_unmeasured_tasks='Older tasks without structured attempt metrics are not estimated or double-counted.')


def estimate(journal,provider,home,model,effort,requirements,profile):
    rows=journal.all('SELECT started_at,completed_at,payload_json FROM task_usage WHERE provider=? AND home=? AND model=? AND effort IS ? ORDER BY completed_at DESC LIMIT 128',
        (provider,str(home),model,effort))
    samples=[];durations=[]
    for row in rows:
        p=json.loads(row['payload_json'])
        if p.get('status')!='completed' or p.get('requirements')!=requirements or p.get('profile')!=profile:continue
        tokens=p.get('tokens',{})
        if 'input_tokens' in tokens and 'output_tokens' in tokens:
            samples.append(tokens['input_tokens']+tokens['output_tokens'])
            durations.append(max(0,row['completed_at']-row['started_at']))
    if len(samples)<5:return dict(state='UNKNOWN',sample_count=len(samples),reason='At least five comparable measured attempts are required.')
    quartiles=statistics.quantiles(samples,n=4,method='inclusive')
    return dict(state='ESTIMATE',sample_count=len(samples),median_tokens=statistics.median(samples),
        token_interval=[quartiles[0],quartiles[2]],median_seconds=statistics.median(durations),
        basis='Same provider/home/model/requested effort/profile/requirements; successful owned attempts.',
        quota_consumption='UNKNOWN: token counts are not subscription allowance percentages.')


def runway(journal,provider,home,scope,current_pool,now,ttl):
    if current_pool['state'] not in ('AVAILABLE','LIMITED') or current_pool.get('unit')!='percent' or current_pool.get('resolution') is None:
        return dict(state='UNKNOWN',reason='Fresh compatible percentage observations with known precision are required.')
    rows=journal.all("SELECT observed_at,payload_json FROM usage_observations WHERE provider=? AND home=? AND scope=? AND kind='snapshot' AND observed_at>=? ORDER BY observed_at DESC LIMIT 32",(provider,str(home),scope,now-86400))
    points=[]
    for row in rows:
        p=next((p for p in json.loads(row['payload_json']).get('pools',[]) if p['pool_id']==current_pool['pool_id']),None)
        if not p or p.get('resets_at')!=current_pool.get('resets_at') or p.get('used') is None:break
        points.append((row['observed_at'],p['used']))
    points.reverse()
    if len(points)<3 or points[-1][0]-points[0][0]<60 or any(b[1]<a[1] for a,b in zip(points,points[1:])):
        return dict(state='UNKNOWN',reason='Need three monotonic observations spanning one minute within the same reset window.')
    span=points[-1][0]-points[0][0];used=points[-1][1]-points[0][1]
    if used<=2*current_pool['resolution']:return dict(state='UNKNOWN',reason='Observed change is too small relative to reported precision.')
    return dict(state='ESTIMATE',seconds_at_recent_pace=current_pool['remaining']/(used/span),sample_count=len(points),
        observation_span_seconds=span,basis='Reported account-wide pace; includes activity outside NativePeers and assumes that pace continues.',
        task_attribution=False)


def recommend(status,requirements,preference,settings):
    require(preference in PREFERENCES,'PROTOCOL_ERROR','Unknown recommendation preference')
    from ..protocol import validate
    from ..profiles import ROOT
    validate(requirements,json.loads((ROOT/'schemas/requirements.v1.json').read_text()))
    candidates=[];excluded=[]
    ranks={'routine':0,'standard':1,'demanding':2}
    for provider in status['providers']:
        for model in provider['models']:
            config=next((m for m in settings['models'] if m['provider']==provider['provider'] and m['model']==model['model']),{})
            reasons=[];fail=[]
            if model.get('support') not in ('NATIVE_CATALOG','NATIVE_SELECTION','OBSERVED_NATIVE'):fail.append('Native support for this candidate has not been observed.')
            if model['state'] in ('EXHAUSTED','AUTH_REQUIRED','MODEL_UNAVAILABLE','CAPABILITY_MISMATCH'):fail.append(model['state'])
            required_quality=requirements.get('quality',requirements.get('complexity','standard'))
            quality=config.get('quality',{}).get(requirements.get('task_type','implementation'))
            if ranks.get(config.get('min_complexity','routine'),0)>ranks[requirements.get('complexity',required_quality)]:fail.append('This model is locally reserved for more demanding tasks.')
            if quality not in ranks or ranks[quality]<ranks[required_quality]:fail.append('Required quality has no sufficient configured, attributed capability rating.')
            if requirements.get('context_tokens',0)>config.get('context_tokens',0):fail.append('Required context capacity is unverified.')
            if set(requirements.get('required_tools',[]))-set(config.get('tools',[])):fail.append('Required tools are unverified.')
            if set(requirements.get('modalities',[]))-set(model.get('modalities',config.get('modalities',[]))):fail.append('Required modalities are unverified.')
            if requirements.get('reasoning_effort') and requirements['reasoning_effort'] not in model.get('efforts',config.get('efforts',[])):fail.append('Required reasoning effort is unverified.')
            independent=requirements.get('independent_from_vendor')
            if independent and (not config.get('vendor') or independent==config['vendor']):fail.append('Independent vendor requirement is not established.')
            if preference=='exact-model-only' and model['model']!=requirements.get('model'):fail.append('Exact-model-only selection excludes this candidate.')
            if fail:excluded.append(dict(provider=provider['provider'],model=model['model'],reasons=fail));continue
            reasons.append('Meets configured task quality and capability requirements; source: '+config['source'])
            reasons.append('Quota state: '+model['state']+'; unknown/stale capacity is not exhaustion.')
            pools=[p for p in provider['pools'] if p['pool_id'] in model['pool_ids'] and p['unit']=='percent' and p['remaining'] is not None]
            headroom=min((p['remaining'] for p in pools),default=None)
            reasons.append('Known percentage headroom: '+('unknown' if headroom is None else str(headroom)+'%; every mapped pool is considered.'))
            # Quality stays first except where a caller explicitly asks to preserve quota.
            quality_rank=ranks[quality];preference_rank=config.get('priority',0)
            efficiency=config.get('efficiency',0) if preference=='fast' else 0
            quota_rank=2 if model['state']=='AVAILABLE' else 1 if model['state']=='LIMITED' else 0
            score=(quota_rank,quality_rank,headroom or 0,preference_rank) if preference=='quota-preserving' else (quality_rank,quota_rank,efficiency,preference_rank,headroom or 0)
            candidates.append(dict(provider=provider['provider'],model=model['model'],state=model['state'],reasons=reasons,
                preference_score=list(score),selection_changed=False,entitlement='UNVERIFIED'))
    candidates.sort(key=lambda c:tuple(c['preference_score']),reverse=True)
    return dict(preference=preference,candidates=candidates[:20],excluded=excluded[:64],selection_changed=False,
        recommended=candidates[0] if candidates else None,
        limitation='Capability ratings are explicit local preferences with sources, not invented empirical rankings. No recommendation rewrites a task.')
