"""Deliberately selected local claims, never upgraded to native evidence."""
import json
import time

from ..errors import require
from ..profiles import ROOT
from ..protocol import validate
from .normalize import pool
from .service import Usage
from .store import record


def import_snapshot(journal,caller,value):
    require(not caller.grants.get('worker_id'),'APPROVAL_BLOCKED','Owned workers cannot import usage claims')
    validate(value,json.loads((ROOT/'schemas/usage-import.v1.json').read_text()))
    require(value['observed_at']<=time.time()+5,'PROTOCOL_ERROR','Imported observation is in the future')
    service=Usage(journal);provider=value['provider'];home=service.home(provider,caller,value.get('home_label'))
    pools=[]
    for item in value['pools']:
        data=dict(item);identifier=data.pop('pool_id');reset=data.pop('resets_at',None);duration=data.pop('window_seconds',None)
        p=pool(identifier,reset=reset,duration=duration,**data);p.update(basis='user-supplied',membership='user-supplied')
        pools.append(p)
    payload=dict(source='explicit-local-import',source_version=1,route='unknown',coverage='user-supplied',pools=pools,
        models=[dict(model=m,capability_source='user-supplied',entitlement='UNVERIFIED') for m in value.get('models',[])],
        limitations=['Imported claims are not provider-verified and cannot automatically block native submission.'])
    identifier=record(journal,provider,home,payload,kind='import',at=value['observed_at'])
    return dict(observation_id=identifier,provider=provider,home=str(home),basis='user-supplied',provider_verified=False)
