"""Bounded observations in the existing journal; raw native inputs are not stored."""
import json
import os
from pathlib import Path
import time

from ..files import canonical,sha256
from .collectors import executable


def scope(provider,home):
    # Metadata only: never open credential files or query a credential store.
    markers=[]
    names={'codex':['auth.json','config.toml'],'claude':['.credentials.json','settings.json'],
           'antigravity':['settings.json']}[provider]
    for name in names:
        path=Path(home)/name
        try:
            s=path.stat();markers.append((name,s.st_dev,s.st_ino,s.st_mtime_ns,s.st_size))
        except OSError:markers.append((name,None))
    path=executable(provider)
    try:
        s=path.resolve().stat();runtime=(str(path.resolve()),s.st_size,s.st_mtime_ns)
    except OSError:runtime=None
    flags={k:bool(os.environ.get(k)) for k in {
        'claude':('ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN','ANTHROPIC_BASE_URL','CLAUDE_CODE_USE_BEDROCK','CLAUDE_CODE_USE_VERTEX','CLAUDE_CODE_USE_FOUNDRY'),
        'codex':('OPENAI_API_KEY','OPENAI_BASE_URL'),'antigravity':()}[provider]}
    return sha256(dict(provider=provider,home=str(Path(home).resolve()),markers=markers,runtime=runtime,route_flags=flags))


def latest(journal,provider,home,kind=None,*,active=False):
    suffix=' AND kind=?' if kind else ''
    params=[provider,str(Path(home).resolve()),scope(provider,home)]
    if kind:params.append(kind)
    if active:suffix+=" AND json_type(payload_json,'$.acquisition_id')='text'"
    row=journal.one('SELECT * FROM usage_observations WHERE provider=? AND home=? AND scope=?'+suffix+' ORDER BY observation_id DESC LIMIT 1',params)
    if row:row['payload']=json.loads(row.pop('payload_json'))
    return row


def record(journal,provider,home,payload,*,kind='snapshot',at=None,dedupe=False,settings=None):
    from .config import load
    settings=settings or load(journal)
    at=time.time() if at is None else at
    home=str(Path(home).resolve());identity=scope(provider,home)
    raw=canonical(payload).decode()
    previous=latest(journal,provider,home,kind)
    if dedupe and previous and canonical(previous['payload']).decode()==raw:
        return previous['observation_id']
    with journal.transaction() as db:
        cursor=db.execute('INSERT INTO usage_observations(provider,home,scope,kind,observed_at,payload_json) VALUES(?,?,?,?,?,?)',
            (provider,home,identity,kind,at,raw))
        observation=cursor.lastrowid
        db.execute('DELETE FROM usage_observations WHERE provider=? AND home=? AND observation_id NOT IN '
            '(SELECT observation_id FROM usage_observations WHERE provider=? AND home=? ORDER BY observation_id DESC LIMIT ?) '
            'AND observation_id NOT IN (SELECT MAX(observation_id) FROM usage_observations WHERE provider=? AND home=? GROUP BY kind)',
            (provider,home,provider,home,max(1,settings['observations_per_scope']-5),provider,home))
        db.execute('DELETE FROM usage_observations WHERE observed_at<? AND observation_id NOT IN '
            '(SELECT MAX(observation_id) FROM usage_observations GROUP BY provider,home,kind)',(at-settings['retention_days']*86400,))
    return observation


def native_error(journal,row,home,error):
    if error.get('suppressed') or (error.get('details') or {}).get('usage'):return
    code=error.get('code')
    if code not in ('QUOTA_EXHAUSTED','RATE_LIMITED','AUTH_REQUIRED','MODEL_UNAVAILABLE','CAPABILITY_MISMATCH'):return
    # A quota refusal is useful even after submission, but is never replay proof.
    state='EXHAUSTED' if code=='QUOTA_EXHAUSTED' else 'LIMITED' if code=='RATE_LIMITED' else code
    from .normalize import pool,timestamp
    reset=timestamp(error.get('resets_at') or error.get('reset_at'))
    pending=[error]
    for _ in range(128):
        if not pending or reset is not None:break
        item=pending.pop()
        if isinstance(item,dict):
            for key in ('reset_at','resets_at','reset_time','resetTime'):
                reset=timestamp(item.get(key))
                if reset is not None:break
            pending.extend(v for v in item.values() if isinstance(v,(dict,list)))
        elif isinstance(item,list):pending.extend(item[:128])
    record(journal,row['provider'],home,dict(source='native-task-error',source_version=1,
        route='native-auth-unknown',coverage='exact-refusal',models=[],
        pools=[pool('task-refusal/'+row['model_requested'],state=state,reset=reset,models=[row['model_requested']],
            limiting_scope=error.get('limiting_scope') or error.get('pool_id'),
            kind='temporary-rate-limit' if code=='RATE_LIMITED' else 'model-specific',membership='exact-native-refusal',
            error_code=code,may_have_received=bool(error.get('may_have_received')),
            reset_note=str(error.get('reset_time',''))[:256])],
        limitations=['A refusal is not a complete account usage snapshot or permission to replay task input.']),kind='native-error')
