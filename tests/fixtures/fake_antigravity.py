#!/usr/bin/env python3
"""Native-shaped Antigravity stream; never contacts a model provider."""
import json
import os
from pathlib import Path
import sys
import time
import uuid

args=sys.argv[1:]
native=args[args.index('--conversation')+1] if '--conversation' in args else str(uuid.uuid4())
message=json.loads(sys.stdin.readline())
def emit(value):print(json.dumps(value),flush=True)
emit({'event':'init','conversation_id':native,'init':{'tools':['view_file','grep_search']}})
from nativepeers.journal import Journal
j=Journal();binding=json.loads(Path(os.environ['NPEER_BOOTSTRAP']).read_text())
j.event(binding['grants']['worker_id'],'native-hook-observed',{'native_id':native,'event':'PreInvocation','model':args[args.index('--model')+1]});j.close()
time.sleep(.15)
result={'event':'result','result':{'conversation_id':native,'status':'SUCCESS','response':'fixture result '+message['message']['content'].splitlines()[-1],'num_turns':1}}
data=json.dumps(result);sys.stdout.write(data[:20]);sys.stdout.flush();time.sleep(.02);print(data[20:],flush=True)
time.sleep(.1)
