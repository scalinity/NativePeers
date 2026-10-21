#!/usr/bin/env python3
"""Structured Claude fixture with a terminal result preceding process exit."""
import json
import sys
import time

args=sys.argv[1:]
native=args[args.index('--session-id')+1] if '--session-id' in args else args[args.index('--resume')+1]
task=sys.stdin.read()
def emit(value):print(json.dumps(value),flush=True)
emit({'type':'system','subtype':'init','session_id':native,'model':'fake-claude','tools':['Read']})
time.sleep(.15)
emit({'type':'assistant','session_id':native,'message':{'content':[{'type':'text','text':'intermediate'}]}})
emit({'type':'result','subtype':'success','session_id':native,'result':'fixture result '+task.splitlines()[0],'num_turns':1,'is_error':False})
time.sleep(.15)
