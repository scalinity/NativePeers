"""Omitted-request defaults and optional operator ceilings."""
import json
from .errors import require

DEFAULTS={'workers_per_group':4,'workers_total':8,'max_depth':1,'task_wall_seconds':None,
          'group_wall_seconds':None,'max_task_turns':None,'task_wall_seconds_default':1800,'max_task_turns_default':8,
          'native_max_turns':None,'native_max_turns_default':128,'pending_messages':32,'exchange_hops':8,
          'request_expiry_seconds':1800,'followup_grace_seconds':600,'home_labels':{},'usage':{}}


def load(journal):
    path=journal.root/'policy.json'
    supplied=json.loads(path.read_text()) if path.exists() else {}
    require(isinstance(supplied,dict) and not set(supplied)-set(DEFAULTS),'PROTOCOL_ERROR','Unknown local policy fields')
    result=dict(DEFAULTS,**supplied)
    for key in DEFAULTS:
        if key in ('task_wall_seconds','group_wall_seconds','max_task_turns','native_max_turns') and result[key] is None:continue
        if key not in ('home_labels','usage'):require(type(result[key]) is int and result[key]>0,'PROTOCOL_ERROR','Invalid policy limit: '+key)
    require(isinstance(result['home_labels'],dict) and all(isinstance(k,str) and isinstance(v,str) and v and ':' not in v and '@' not in v for k,v in result['home_labels'].items()),'PROTOCOL_ERROR','Invalid provider home labels')
    from .usage.config import validate
    result['usage']=validate(result['usage'])
    result['sources']={key:dict(policy_key=key,source='policy.json' if key in supplied else 'built-in default',policy_file=str(path)) for key in DEFAULTS}
    return result


def spawn_limits(local,requested=None):
    requested=requested or {};result={}
    for field,key in (('wall_time_seconds','task_wall_seconds'),('max_task_turns_per_worker','max_task_turns'),('max_native_turns_per_task','native_max_turns')):
        default=key+'_default';effective=local[default];source=local['sources'][default]['source']
        for ceiling in (key,'group_wall_seconds') if field=='wall_time_seconds' else (key,):
            if local[ceiling] is not None and effective>local[ceiling]:
                effective=local[ceiling];source=local['sources'][ceiling]['source']
        if field in requested:effective=requested[field];source='spawn spec'
        result[field]=dict(effective=effective,source=source,default=dict(value=local[default],**local['sources'][default]),
                           ceiling=dict(value=local[key],**local['sources'][key]))
    result['group_wall_seconds']=dict(effective=local['group_wall_seconds'],**local['sources']['group_wall_seconds'])
    return result


def home_label(journal,home):
    """Canonical path keys are current; hashed keys are explicit legacy support."""
    from pathlib import Path
    from .models import home_id
    home=Path(home).resolve();labels=load(journal)['home_labels']
    selected={value for key,value in labels.items() if Path(key).is_absolute() and Path(key).expanduser().resolve()==home}
    if home_id(home) in labels:selected.add(labels[home_id(home)])
    require(len(selected)<=1,'AMBIGUOUS_TARGET','Canonical and legacy provider-home labels disagree')
    return next(iter(selected),home.name)
