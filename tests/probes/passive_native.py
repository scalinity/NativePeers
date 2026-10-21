#!/usr/bin/env python3
"""Two zero-input sacrificial native TUIs: installed hooks, duplicate titles, cleanup."""
import asyncio,json,os,pty,signal,subprocess,sys,time,fcntl,termios,struct
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from nativepeers.errors import PeerError
from nativepeers.files import atomic_json
from nativepeers.journal import Journal
from nativepeers.launch import launch_environment
from nativepeers.process import identity,matches,signal_owned
from nativepeers.profiles import config_arguments
from nativepeers.registry import Registry

def names():
 result={}
 path=Path.home()/'.codex/session_index.jsonl'
 for line in path.open():
  value=json.loads(line)
  if value.get('thread_name')=='NP-ZERO-DUPLICATE':result[value['id']]=value
 return result


async def main():
 j=Journal(ROOT/'evidence/private/live-state');registry=Registry(j);children=[];fixture=ROOT/'evidence/scratch/passive-workspace';fixture.mkdir(exist_ok=True)
 binary=(Path.home()/'.local/share/codex-mcp-manager/current/bin/codex').resolve();overrides=json.loads((ROOT/'evidence/passive/live-parent-overrides.json').read_text());overrides.pop('hooks.state',None);overrides['projects']={str(fixture):{'trust_level':'trusted'}}
 env=launch_environment('codex');env.update(NPEER_HOME=str(j.root),PYTHONPATH=str(ROOT),TERM='xterm-256color')
 previous=set(names())
 try:
  for _ in range(2):
   master,slave=pty.openpty();fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',30,120,0,0));p=subprocess.Popen([str(binary),'--name','NP-ZERO-DUPLICATE','--dangerously-bypass-approvals-and-sandbox',*config_arguments(overrides)],cwd=fixture,env=env,stdin=slave,stdout=slave,stderr=slave,start_new_session=True,close_fds=True);os.close(slave);os.set_blocking(master,False);children.append({'process':p,'master':master,'owner':identity(p.pid).json(),'output':bytearray()})
  deadline=time.time()+20;created=set()
  while time.time()<deadline:
   for c in children:
    try:c['output'].extend(os.read(c['master'],65536))
    except (BlockingIOError,OSError):pass
   created=set(names())-previous
   if len(created)==2:break
   await asyncio.sleep(.1)
  assert len(created)==2,'Native empty naming index did not preserve two distinct IDs'
  assert all(matches(c['owner']) for c in children)
  proof={'status':'PASS','evidence_class':'installed-passive','top_level_model_inputs':0,
   'scope':'Native empty startup names retained; native SessionStart hooks are deferred to first turn, so these empty TUIs are not registered NativePeers callers.',
   'distinct_empty_native_ids':sorted(created),'native_hook_registration':'NOT_RUN before first native turn',
   'duplicate_routing_live':'NOT_RUN; tested by deterministic contracts only','owner_processes':[c['owner'] for c in children]}

 finally:
  for c in children:signal_owned(c['owner'],signal.SIGTERM,group=True)
  for c in children:
   try:c['process'].wait(timeout=5)
   except subprocess.TimeoutExpired:signal_owned(c['owner'],signal.SIGKILL,group=True);c['process'].wait(timeout=3)
   os.close(c['master'])
 if 'proof' in locals():
  proof['owned_owners_ended']=all(not matches(c['owner']) for c in children)
  proof['native_name_retained_after_owner_exit']=set(created)<=set(names());atomic_json(ROOT/'evidence/passive-native.json',proof);print(json.dumps(proof))
 j.close()

if __name__=='__main__':asyncio.run(main())
