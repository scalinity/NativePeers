#!/usr/bin/env python3
"""Deterministic private-stdio provider. Never contacts a model or native history."""
import json
import os
import sys
import time
import uuid

native=str(uuid.uuid4())
def emit(value):
    raw=json.dumps(value,ensure_ascii=False).encode()+b'\n'
    # Deliberately split arbitrary bytes, including potential UTF-8 code units.
    for start in range(0,len(raw),37):sys.stdout.buffer.write(raw[start:start+37]);sys.stdout.buffer.flush()

for line in sys.stdin.buffer:
    request=json.loads(line);method=request.get('method');params=request.get('params',{})
    if 'id' not in request:continue
    result={}
    if method=='initialize':result={'userAgent':'fake-codex','codexHome':'/fake','nativepeersWorkerSource':'--nativepeers-worker' in sys.argv}
    elif method=='model/list':result={'data':[{'model':'gpt-6.1-sol','supportedReasoningEfforts':[{'reasoningEffort':'xhigh'}]}]}
    elif method in ('thread/start','thread/resume'):
        native=params.get('threadId',native);result={'thread':{'id':native,'source':{'custom':'nativepeers-worker'} if '--nativepeers-worker' in sys.argv else 'cli'},'model':params.get('model')}
    elif method=='mcpServerStatus/list':result={'data':[{'name':'nativepeers','tools':{'peer_send':{}},'runtimeStatus':'connected'}]}
    elif method=='turn/start':
        turn=str(uuid.uuid4());result={'turn':{'id':turn,'status':'inProgress','items':[]}}
        emit({'id':request['id'],'result':result});text=params['input'][0]['text']
        if 'DROP_TERMINAL' in text:sys.exit(0)
        time.sleep(.15)
        item={'type':'agentMessage','id':str(uuid.uuid4()),'text':'fixture result 💡 '+text.splitlines()[0],'phase':'final_answer'}
        emit({'method':'item/completed','params':{'threadId':native,'turnId':turn,'item':item}})
        emit({'method':'turn/completed','params':{'threadId':native,'turn':{'id':turn,'status':'completed','items':[item]}}})
        continue
    emit({'id':request['id'],'result':result})
