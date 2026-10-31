#!/usr/bin/env python3
"""Explicit, budgeted input to the two already owned disposable native parents."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from nativepeers.adapters.native_delivery import claude_inbox,codex_queue
from nativepeers.budget import LiveBudget
from nativepeers.files import atomic_json,private_directory
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.registry import Registry

STATE=ROOT/'evidence/private/live-state'
COMMAND=f'NPEER_HOME={STATE} PYTHONPATH={ROOT} {ROOT}/.venv/bin/python -m nativepeers'


def parents(journal):
    peers=Registry(journal).list()
    return {p['peer_key']['provider']:p for p in peers if p['title'] in ('NP-FINAL-CLAUDE-PARENT','NP-LIVE-08B')}


async def submit(provider,text,label):
    journal=Journal(STATE)
    try:
        peer=parents(journal)[provider];budget=LiveBudget(ROOT/'evidence/live-budget.json')
        identifier=new_id();now=time.time()
        envelope={'schema_version':1,'message_id':identifier,'exchange_id':new_id(),'kind':'notify','origin':'explicit-disposable-test-harness',
           'created_at':now,'expires_at':now+1800,'from':{'kind':'external-local','source_label':'authorized NativePeers acceptance harness'},
           'to':{'peer_key':peer['handle']},'body':{'text':text},'expect_reply':False}
        run=private_directory(ROOT/'evidence/private/final-batch')
        record={'provider':provider,'native_id':peer['peer_key']['native_session_id'],'reservation':None,'message_id':identifier,'label':label,'text':text,'submitted_at':now,
            'source_dependencies':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'nativepeers').rglob('*.py'))}}
        atomic_json(run/(identifier+'.json'),record)
        def admit():
            record['reservation']=budget.reserve(label,tranche='remaining_mandatory')
            budget.admit(record['reservation']);atomic_json(run/(identifier+'.json'),record)
        try:
            if provider=='codex':receipt=await codex_queue(peer,envelope,journal=journal,before_launch=admit)
            else:
                admit();receipt=await claude_inbox(peer,envelope)
        except BaseException as error:record['error']=str(error);atomic_json(run/(identifier+'.json'),record);raise
        record['receipt']=receipt;atomic_json(run/(identifier+'.json'),record)
        print(json.dumps({'native_id':record['native_id'],'reservation':record['reservation'],'message_id':identifier,'receipt':receipt}))
    finally:journal.close()


def worker(provider,name,nonce,sibling=None):
    task=f'Read the fixture values alpha=7 beta=11 gamma=13. Your assigned nonce is {nonce}. Report your own native worker title, nonce and the sum 31. '
    if sibling:
        task+=f'Use the bound NativePeers peer_send tool exactly once to target {provider}:{sibling} with text "SIBLING-QUESTION {nonce}: what is the largest fixture value? Reply through peer_reply to this immediate message author once." Set expect_reply=true and client_request_id="{nonce}-question". Finish your own initial result now; do not wait or poll. '
    task+='If a later NativePeers request explicitly asks for a reply, use peer_reply exactly once to its message_id with your own finding and the supplied nonce, expect_reply=false and a new stable client_request_id. For a reply with expect_reply=false, report the finding without sending anything else. Never start other workers or vendor-native agents.'
    spec={'provider':provider,'title':name,'task':task,'permission_profile':'analysis','workspace':{'mode':'inherit-read-only'}}
    if provider=='codex':spec.update(model='gpt-6.1-sol',reasoning_effort='xhigh')
    return spec


async def main():
    mode=sys.argv[1]
    if mode=='delegations':
        atomic_json(STATE/'test-budget.json',{'path':str(ROOT/'evidence/live-budget.json'),'tranche':'remaining_mandatory'})
        run=private_directory(ROOT/'evidence/private/final-batch')
        for parent,other in [('claude','codex'),('codex','claude')]:
            prefix='NP-FINAL-'+parent.upper()
            workers=[worker(other,prefix+' cross A',parent+'-cross-A',prefix+' cross B'),worker(other,prefix+' cross B',parent+'-cross-B'),
                     worker(parent,prefix+' same A',parent+'-same-A'),worker(parent,prefix+' same B',parent+'-same-B')]
            spec={'schema_version':1,'client_request_id':prefix+'-batch-1','completion_mode':'push' if parent=='claude' else 'wait','allow_sibling_messages':True,'workers':workers,
                  'limits':{'max_concurrent_workers':4,'max_depth':1,'wall_time_seconds':180,'max_task_turns_per_worker':8,'followup_grace_seconds':120}}
            atomic_json(run/(parent+'-spawn.json'),spec)
        for parent in ('claude','codex'):
            path=run/(parent+'-spawn.json')
            task=f'Explicit disposable NativePeers final acceptance. Use your native shell tool to run exactly once: {COMMAND} spawn --spec {path} --json . Use the actual returned group ID in the next command. '
            if parent=='claude':task+=f'Run {COMMAND} wait GROUP_ID --timeout-ms 1 --on-timeout push --json . A WAIT_EXPIRED exit is expected. End this turn with FINAL-BATCH-WAITING claude. When the controller completion arrives, report all four worker native IDs, titles, assigned nonces and results.'
            else:task+=f'Run {COMMAND} wait GROUP_ID --timeout-ms 30000 --on-timeout retain --json . If it is ready, report FINAL-BATCH-CURRENT-WAIT codex with all four worker native IDs, titles, assigned nonces and results. If not ready, call bounded wait again once. Never poll status repeatedly.'
            task+=' Do not perform the worker tasks yourself or launch vendor-native agents. For later peer requests with expect_reply=true, follow their explicit reply instructions once; for expect_reply=false, acknowledge the finding without sending a reply.'
            await submit(parent,task,'final current-code '+parent+' delegation parent input')
    elif mode=='input':await submit(sys.argv[2],Path(sys.argv[3]).read_text(),sys.argv[4])
    else:raise ValueError(mode)

if __name__=='__main__':asyncio.run(main())
