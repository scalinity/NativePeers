#!/usr/bin/env python3
"""Opt-in native Flash acceptance, with a deterministic non-provider Claude fixture."""
import argparse
import asyncio
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sys
import time

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from nativepeers.antigravity import catalog
from nativepeers.budget import LiveBudget
from nativepeers.caller import bind_caller
from nativepeers.compatibility import fingerprint
from nativepeers.core import Core
from nativepeers.delegation import Delegation,TERMINAL
from nativepeers.files import atomic_json,private_directory,canonical
from nativepeers.journal import Journal
from nativepeers.launch import launch_environment
from nativepeers.models import new_id
from nativepeers.process import identity,matches,signal_owned
from nativepeers.worktrees import git,snapshot

BASE=ROOT/'evidence/private/flash-fallback-20261022'
FIXTURE=BASE/'fixture'
RUN=FIXTURE/'.nativepeers-test/run-5'
STATE=RUN/'state';LEDGER=ROOT/'evidence/live-budget.json'
MODEL='gemini-3.8-flash-medium'


def stamp():return datetime.now(__import__('zoneinfo').ZoneInfo('America/New_York')).isoformat()
def sources():return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'nativepeers').rglob('*.py'))}
async def prepare():
 private_directory(BASE)
 if not FIXTURE.exists():FIXTURE.mkdir(mode=0o700)
 if not (FIXTURE/'.git').exists():
  (FIXTURE/'fixture.txt').write_text('VALUE=1\n')
  git(FIXTURE,'init','-q');git(FIXTURE,'add','fixture.txt');git(FIXTURE,'commit','-qm','Create native fallback verification fixture')
  (FIXTURE/'fixture.txt').write_text('VALUE=2\n')
 assert not (RUN/'prepared.json').exists(),'Use a new --run-id; retained captures must not be overwritten'
 private_directory(RUN)
 assert (FIXTURE/'fixture.txt').read_text()=='VALUE=2\n'
 exclude=FIXTURE/'.git/info/exclude'
 if '.nativepeers-test/' not in exclude.read_text():
  with exclude.open('a') as stream:stream.write('\n.nativepeers-test/\n')
 fake=RUN/'fake-claude'
 fake.write_text('#!'+sys.executable+'\n'+'''import json,sys
args=sys.argv[1:];native=args[args.index('--session-id')+1];model=args[args.index('--model')+1]
sys.stdin.read()
def emit(value):print(json.dumps(dict(value,session_id=native)),flush=True)
emit({'type':'system','subtype':'init','model':model,'tools':['Read']})
emit({'type':'assistant','isApiErrorMessage':True,'error':'rate_limit_error','message':{'model':'<synthetic>','usage':{'input_tokens':0,'output_tokens':0},'content':[{'type':'text','text':"You've hit your limit"}]}})
emit({'type':'result','subtype':'error_during_execution','is_error':True,'usage':{'input_tokens':0,'output_tokens':0},'num_turns':1})
''');fake.chmod(0o700)
 journal=Journal(STATE)
 try:
  production=json.loads((Path.home()/'.local/state/nativepeers/compat/runtime.json').read_text())
  binary=Path.home()/'.local/bin/agy';info=fingerprint(binary)
  assert info['sha256']==production['providers']['antigravity']['sha256']
  advertised=await catalog(binary);assert MODEL in advertised
  # Only this isolated fixture receipt permits the fake executable. Native
  # provider homes/authentication and production receipts are never changed.
  atomic_json(STATE/'compat/runtime.json',{'providers':{'claude':fingerprint(fake),'antigravity':production['providers']['antigravity']}})
  atomic_json(STATE/'policy.json',{'task_wall_seconds':240,'group_wall_seconds':300,'followup_grace_seconds':1})
  atomic_json(RUN/'prepared.json',{'at':stamp(),'source':sources(),'provider':info,'model':MODEL,'model_display_name':advertised[MODEL],'simulated_claude':str(fake),'native_inputs':0})
 finally:journal.close()
 print('Prepared isolated fixture; exact Flash catalog and executable verified; zero model inputs.')


class FixtureDelegation(Delegation):
 async def ensure_supervisor(self,group_id):
  for row in self.journal.all('SELECT * FROM workers WHERE group_id=?',(group_id,)):
   assert row['provider']=='claude' and row['state']=='RESERVED'
   lease=json.loads(row['lease_json']);lease['executable']=str(RUN/'fake-claude')
   self.journal.db.execute('UPDATE workers SET lease_json=? WHERE worker_id=?',(json.dumps(lease),row['worker_id']))
  return await super().ensure_supervisor(group_id)


async def parent():
 journal=Journal(STATE);core=Core(journal)
 caller=bind_caller(journal,core.registry,allow_external=False)
 peer=core.registry.verify(caller.key,caller.incarnation);assert peer['peer_key']['provider']=='antigravity'
 assert Path(peer['cwd']).resolve()==FIXTURE,'The native parent must own the fixture as its primary workspace'
 # Prevent a model retry of the helper from admitting a second batch.
 with (RUN/'helper-started').open('x') as stream:stream.write(peer['peer_key']['native_session_id'])
 prepared=json.loads((RUN/'prepared.json').read_text());assert sources()==prepared['source']
 report={'at':stamp(),'parent_native_id':peer['peer_key']['native_session_id'],'source':sources(),'groups':[],'checks':{}}
 atomic_json(RUN/'parent-case.json',report)
 try:
  for mode in ('wait','push'):
   nonce='NP-FLASH-'+new_id().replace('-','')
   private_expected={'nonce':nonce,'nonce_sha256':hashlib.sha256(nonce.encode()).hexdigest()}
   atomic_json(RUN/(mode+'-expected.json'),private_expected)
   editing=mode=='wait'
   task=('Do not delegate, start agents, send peer messages or inspect other directories. ')
   if editing:task+='Read fixture.txt. Create only answer.py containing def answer(): return 42. Run a real Python assertion that answer()==42. Report the exact marker '+nonce+' and the actual check. '
   else:task+='Return exactly '+nonce+' as your complete final answer. Use no tools. '
   spec={'client_request_id':'flash-fallback-'+mode,'completion_mode':mode,'workers':[{'provider':'claude','model':'claude-sonnet-5-5','title':'Flash fallback '+mode,
    'task':task,'permission_profile':'workspace' if editing else 'analysis','workspace':{'mode':'isolated-worktree' if editing else 'inherit-read-only'},
    'fallbacks':[{'provider':'antigravity','model':MODEL}]}],
    'limits':{'max_concurrent_workers':1,'max_depth':1,'wall_time_seconds':240,'max_task_turns_per_worker':3,'followup_grace_seconds':1}}
   before=snapshot(FIXTURE)
   value=await FixtureDelegation(journal,core.registry).spawn(caller,spec);group=value['group_id'];report['groups'].append(group)
   atomic_json(RUN/'parent-case.json',report)
   async with asyncio.timeout(270):
    while any(t['state'] not in TERMINAL for t in core.delegation.status(caller,group)['tasks']):await asyncio.sleep(.1)
   value=core.delegation.result(caller,group);record=value['results'][0]
   atomic_json(RUN/(mode+'-result.json'),value)
   checks={'completed':record['status']=='completed','flash_requested':record['model_requested']==MODEL,'flash_observed':record['model_observed']==MODEL,
    'two_retained_attempts':len(record['attempts'])==2,'primary_quota':record.get('fallback',{}).get('primary_failure',{}).get('code')=='QUOTA_EXHAUSTED',
    'distinct_native_ids':len({a['native_id'] for a in record['attempts']})==2,
    'parent_distinct':all(a['native_id']!=report['parent_native_id'] for a in record['attempts']),
    'nonce_in_worker_result':nonce in record['summary'],'source_preserved':snapshot(FIXTURE)==before}
   old=record['attempts'][0]['worker_id'];manifest=json.loads((STATE/'workers'/(old+'.json')).read_text())
   checks['old_controller_exited']=not matches(manifest['owner'])
   checks['old_native_exited']=not manifest.get('native_process') or not matches(manifest['native_process'])
   if editing:
    directory=Path(record['changes']['worktree']);checks['isolated_directory']=directory!=FIXTURE
    import runpy
    checks['independent_check']=runpy.run_path(str(directory/'answer.py'))['answer']()==42
    checks['reconstruction_patch']=bool(record['changes'].get('patch_artifact'))
    checks['worker_delta']=bool(record['changes'].get('worker_delta_artifact'))
    waited=await core.delegation.wait(caller,group,30000,'retain');checks['original_parent_wait']=waited['ready'] and nonce in waited['result']['results'][0]['summary']
    checks['wait_claimed']=journal.one('SELECT state FROM completion_routes WHERE group_id=?',(group,))['state']=='NOTIFICATION_UNKNOWN'
   else:
    # The parent's shell output intentionally withholds this result/nonce. A
    # later native parent answer can obtain it only from the readable inbox.
    async with asyncio.timeout(15):
     while not journal.one('SELECT message_id FROM messages WHERE from_actor_key LIKE ? AND to_key=?',('controller:'+group+':%',caller.key)):await asyncio.sleep(.1)
    message=journal.one('SELECT * FROM messages WHERE from_actor_key LIKE ? AND to_key=?',('controller:'+group+':%',caller.key))
    atomic_json(RUN/'push-delivery.json',{'message_id':message['message_id'],'parent_native_id':report['parent_native_id']})
    checks['original_parent_target']=message['to_key']==caller.key
    from nativepeers.presentation import render
    shown=render(json.loads(message['payload_json']));(RUN/'readable-notice.txt').write_text(shown)
    checks['readable_frame']=shown.startswith('NativePeers · Worker results\n') and nonce in shown and '"schema_version"' not in shown
   report['checks'][mode]=checks;atomic_json(RUN/'parent-case.json',report)
   assert all(checks.values()),checks
  # Exercise the new bound MCP surface from this same actual native tool ancestry.
  from mcp import ClientSession
  from mcp.client.stdio import StdioServerParameters,stdio_client
  params=StdioServerParameters(command=sys.executable,args=['-m','nativepeers.mcp_server','--native-caller'],cwd=ROOT,
      env={'NPEER_HOME':str(STATE),'PYTHONPATH':str(ROOT)})
  async with stdio_client(params) as (reader,writer):
   async with ClientSession(reader,writer,read_timeout_seconds=40) as session:
    await session.initialize()
    async def call(name,args):
     value=json.loads((await session.call_tool(name,args)).content[0].text)
     assert value['ok'],value
     return value['data']
    bound=await call('task_status',{})
    count=journal.one('SELECT COUNT(*) n FROM groups')['n']
    plan=await call('worker_spawn',{'check':True,'spec':{'workers':[{'provider':'antigravity','model':MODEL,'title':'metadata check only','task':'Do not submit this check','permission_profile':'analysis','workspace':{'mode':'inherit-read-only'}}],'limits':{'max_concurrent_workers':1}}})
    worker=record['attempts'][-1]['worker_id']
    async with asyncio.timeout(30):
     while journal.one('SELECT state FROM workers WHERE worker_id=?',(worker,))['state']!='RELEASED_RESUMABLE':await asyncio.sleep(.05)
    marker='NP-RESUME-'+new_id()
    args={'worker_id':worker,'task':'Return exactly '+marker+' with no tools or delegation.','completion_mode':'wait','client_request_id':'flash-live-mcp-resume'}
    atomic_json(RUN/'resume-launch.json',{'worker_id':worker,'native_id':record['worker']['native_session_id']})
    resumed=await call('worker_resume',args)
    async with asyncio.timeout(240):
     while True:
      waited=await call('task_wait',{'handle':resumed['task_id'],'timeout_ms':30000,'on_timeout':'retain'})
      if waited['ready']:break
    resumed_result=waited['result'];atomic_json(RUN/'resume-result.json',resumed_result)
    final=next(v for v in resumed_result['results'] if v['task_id']==resumed['task_id'])
    retry=await call('worker_resume',args)
    groups=await call('task_groups',{'limit':20})
    summary=next(g for g in groups['groups'] if g['group_id']==resumed['group_id'])
    checks={'bound_original_parent':bound['native_session_id']==report['parent_native_id'],
     'read_only_mcp_check':plan['read_only'] and plan['ready'] and journal.one('SELECT COUNT(*) n FROM groups')['n']==count,
     'same_owned_native_history':final['worker']['native_session_id']==record['worker']['native_session_id'],
     'resume_completed':final['status']=='completed' and marker in final['summary'],
     'exact_flash':final['model_observed']==MODEL,'idempotent_resume':retry['task_id']==resumed['task_id'] and retry['state']=='COMPLETED',
     'phase_progress':summary['progress']['latest_completion_phase']==2 and summary['progress']['active_tasks']==0,
     'pinned_runtime':bool(json.loads(journal.one('SELECT lease_json FROM workers WHERE worker_id=?',(worker,))['lease_json']).get('verified_runtime'))}
    report['checks']['mcp']=checks;atomic_json(RUN/'parent-case.json',report);assert all(checks.values()),checks
  print(json.dumps({'status':'PASS','parent_native_id':report['parent_native_id'],'groups':report['groups'],'checks':report['checks'],'push_result':'withheld; read the NativePeers completion injected at your next native invocation'}))
 except Exception as error:
  report['error']={'type':type(error).__name__,'message':str(error)[:1500]};atomic_json(RUN/'parent-case.json',report);raise
 finally:journal.close()


async def run():
 assert not (RUN/'parent-launch.json').exists(),'A native attempt already exists; reconcile it and select a new --run-id'
 prepared=json.loads((RUN/'prepared.json').read_text());assert sources()==prepared['source']
 binary=prepared['provider']['path'];assert fingerprint(binary)['sha256']==prepared['provider']['sha256']
 command=str(ROOT/'.venv/bin/python')+' '+str(ROOT/'tests/live/flash_fallback.py')+' parent --run-id '+RUN.name
 prompt=('Authorized NativePeers Flash-only fallback acceptance. Run exactly this command ONCE with your native run_command tool, in '+str(FIXTURE)+':\n'+command+
  '\nAllow up to 540 seconds for this same command; collect its output if backgrounded. Do not retry, read private test files, change configuration, start agents or solve child tasks yourself. '
  'It uses deterministic fake Claude errors, two real Gemini 3.8 Flash replacement tasks, and one exact owned Flash resume through MCP. After the command returns, an incoming NativePeers worker-results notice will contain the push worker marker. '
  'Return NP-FLASH-PARENT, the parent native UUID from the command, its PASS/FAIL, and copy the NP-FLASH marker from that incoming completion. If the notice is absent say MISSING_COMPLETION. End the turn.')
 env=launch_environment('antigravity');env.update(NPEER_HOME=str(STATE),PYTHONPATH=str(ROOT))
 # The parent is an ordinary native host, not an owned worker. Preserve its
 # configured permissions instead of applying the worker-only sandbox/profile
 # to the host's journal/controller operations. Real children still use the
 # production scoped profiles and --sandbox without any override.
 args=['--input-format','stream-json','--output-format','stream-json',
       '--model',MODEL,'--print-timeout','540s']
 budget=LiveBudget(LEDGER)
 reservations={name:budget.reserve('Native Flash '+name+' input; simulated Claude excluded',tranche='flash_fallback') for name in ('parent','wait','push','resume','completion')}
 for reservation in reservations.values():budget.admit(reservation)
 # The outer collector writes the cumulative ledger. The native parent's
 # sandbox writes only its fixture-local journal and outcome records.
 atomic_json(RUN/'parent-launch.json',{'at':stamp(),'reservations':reservations,'source':sources()})
 process=await asyncio.create_subprocess_exec(binary,*args,cwd=FIXTURE,env=env,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,start_new_session=True,limit=8*1024*1024)
 owner=identity(process.pid).json();atomic_json(RUN/'parent-owner.json',owner)
 process.stdin.write(canonical({'event':'user','message':{'content':prompt}})+b'\n');await process.stdin.drain();process.stdin.close()
 stderr=asyncio.create_task(process.stderr.read(1048576));native=None;terminal=None
 try:
  async with asyncio.timeout(570):
   with (RUN/'parent-stream.jsonl').open('wb') as log:
    while line:=await process.stdout.readline():
     log.write(line);log.flush();event=json.loads(line)
     if event.get('event')=='init':native=event.get('conversation_id');print('Native Flash parent started.',flush=True)
     if event.get('event')=='result':terminal=event['result']
    await process.wait()
  (RUN/'parent-stderr.txt').write_bytes(await stderr)
  atomic_json(RUN/'parent-terminal.json',{'native_id':native,'terminal':terminal,'exit':process.returncode,'at':stamp()})
  budget.settle(reservations['parent'],observed_turns=1,native_id=native)
  for mode in ('wait','push','resume'):
   path=RUN/(mode+'-result.json')
   if path.exists():
    record=json.loads(path.read_text())['results'][0]
    budget.settle(reservations[mode],observed_turns=1,native_id=record['worker']['native_session_id'],completed_at=record['completed_at'])
   else:
    journal=Journal(STATE)
    try:started=(RUN/'resume-launch.json').exists() if mode=='resume' else journal.one("SELECT 1 FROM workers WHERE provider='antigravity' AND role_title=?",('Flash fallback '+mode,))
    finally:journal.close()
    if not started:budget.release_unsubmitted(reservations[mode])
  delivery=RUN/'push-delivery.json'
  if delivery.exists():
   nonce=json.loads((RUN/'push-expected.json').read_text())['nonce']
   seen=nonce in (terminal or {}).get('response','')
   budget.settle(reservations['completion'],observed_turns=1 if seen else None,native_id=native)
  else:
   seen=False;budget.release_unsubmitted(reservations['completion'])
  case=json.loads((RUN/'parent-case.json').read_text()) if (RUN/'parent-case.json').exists() else {}
  checks={'same_parent':case.get('parent_native_id')==native,'helper_pass':bool(case.get('checks')) and len(case['checks'])==3 and all(all(v.values()) for v in case['checks'].values()),'readable_completion_observed':seen,'parent_succeeded':(terminal or {}).get('status')=='SUCCESS'}
  atomic_json(RUN/'verdict.json',{'status':'PASS' if all(checks.values()) else 'FAIL','checks':checks,'parent_native_id':native,'source':sources(),'at':stamp()})
  print(json.dumps({'native_id':native,'exit':process.returncode,'status':(terminal or {}).get('status'),'response':(terminal or {}).get('response','')}),flush=True)
 finally:
  if process.returncode is None:
   signal_owned(owner,signal.SIGTERM,group=True)
   try:await asyncio.wait_for(process.wait(),10)
   except asyncio.TimeoutError:signal_owned(owner,signal.SIGKILL,group=True);await process.wait()
  if not stderr.done():stderr.cancel()


if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','parent','run']);parser.add_argument('--run-id',required=True);args=parser.parse_args()
 assert re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',args.run_id),'Use a single safe run identifier'
 RUN=FIXTURE/'.nativepeers-test'/args.run_id;STATE=RUN/'state'
 asyncio.run({'prepare':prepare,'parent':parent,'run':run}[args.action]())
