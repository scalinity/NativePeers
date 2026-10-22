#!/usr/bin/env python3
"""Native-shaped Antigravity stream; never contacts a model provider."""
import json
import os
from pathlib import Path
import sys
import subprocess
import time
import uuid

args=sys.argv[1:]
native=args[args.index('--conversation')+1] if '--conversation' in args else str(uuid.uuid4())
message=json.loads(sys.stdin.readline())
def emit(value):print(json.dumps(value),flush=True)
emit({'event':'init','conversation_id':native,'init':{'tools':['view_file','grep_search']}})
scenario_path=Path(__file__).with_suffix('.scenario')
scenario=scenario_path.read_text() if scenario_path.exists() else 'success'
if scenario!='no-hook':
    event={'conversationId':native,'modelName':'Gemini Fixture High' if scenario=='display-model' else args[args.index('--model')+1],
           'workspacePaths':[os.getcwd()],'transcriptPath':str(Path.home()/'.gemini/antigravity-cli/brain'/native/'transcript.jsonl')}
    for kind in ['PreInvocation','PreToolUse']:
        if kind=='PreToolUse':event['toolCall']={'name':'view_file','args':{}}
        hook=subprocess.run([sys.executable,'-m','nativepeers.antigravity','hook',kind],input=json.dumps(event),capture_output=True,text=True,check=True)
        if kind=='PreToolUse' and json.loads(hook.stdout).get('decision')!='allow':raise RuntimeError('Actual native hook refused fixture read: '+hook.stderr)
time.sleep(.15)
result={'event':'result','result':{'conversation_id':native,'status':'SUCCESS','response':'fixture result '+message['message']['content'].splitlines()[-1],'num_turns':1}}
if scenario in ('CANCELED','INTERRUPTED'):result['result']['status']=scenario
if scenario=='wrong-id':result['result']['conversation_id']=str(uuid.uuid4())
data=json.dumps(result);sys.stdout.write(data[:20]);sys.stdout.flush();time.sleep(.02);print(data[20:],flush=True)
time.sleep(.1)
