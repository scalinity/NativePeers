"""Editable installation limits; caller requests may only narrow them."""
import json
from .errors import require

DEFAULTS={'workers_per_group':4,'workers_total':8,'max_depth':1,'task_wall_seconds':1800,
          'group_wall_seconds':3600,'max_task_turns':8,'pending_messages':32,'exchange_hops':8,
          'request_expiry_seconds':1800,'followup_grace_seconds':600,'home_labels':{},'usage':{}}


def load(journal):
    path=journal.root/'policy.json'
    supplied=json.loads(path.read_text()) if path.exists() else {}
    require(isinstance(supplied,dict) and not set(supplied)-set(DEFAULTS),'PROTOCOL_ERROR','Unknown local policy fields')
    result=dict(DEFAULTS,**supplied)
    for key in DEFAULTS:
        if key not in ('home_labels','usage'):require(type(result[key]) is int and result[key]>0,'PROTOCOL_ERROR','Invalid policy limit: '+key)
    require(isinstance(result['home_labels'],dict) and all(isinstance(k,str) and isinstance(v,str) and v and ':' not in v and '@' not in v for k,v in result['home_labels'].items()),'PROTOCOL_ERROR','Invalid provider home labels')
    from .usage.config import validate
    result['usage']=validate(result['usage'])
    return result
