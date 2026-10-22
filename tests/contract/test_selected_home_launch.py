"""Selected homes survive real controller launches and automated fake-provider resumes."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import tomllib
import unittest
from unittest.mock import patch

from nativepeers.caller import Caller
from nativepeers.compatibility import fingerprint
from nativepeers.files import atomic_json,canonical,private_directory
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity,matches
from nativepeers.recovery import resume_worker
from nativepeers.registry import Registry
from nativepeers.supervisor import Group


FAKE_CODEX='''
import json,os,sys,uuid
from pathlib import Path
native=str(uuid.uuid4())
def emit(value):print(json.dumps(value),flush=True)
for line in sys.stdin:
 request=json.loads(line);method=request.get('method');params=request.get('params',{})
 if 'id' not in request:continue
 result={}
 if method=='initialize':result={'userAgent':'selected-home-fixture'}
 elif method=='model/list':result={'data':[{'model':'gpt-fixture','supportedReasoningEfforts':[{'reasoningEffort':'xhigh'}]}]}
 elif method in ('thread/start','thread/resume'):
  native=params.get('threadId',native)
  with Path(__file__).with_suffix('.launches').open('a') as log:
   log.write(json.dumps({'home':os.environ.get('CODEX_HOME'),'argv':sys.argv[1:],'method':method,'native':native})+'\\n')
  result={'thread':{'id':native},'model':params['model']}
 elif method=='mcpServerStatus/list':result={'data':[{'name':'nativepeers','tools':{'peer_send':{}}}]}
 elif method=='turn/start':
  turn=str(uuid.uuid4());emit({'id':request['id'],'result':{'turn':{'id':turn}}})
  emit({'method':'turn/completed','params':{'threadId':native,'turn':{'id':turn,'status':'completed','items':[
   {'type':'agentMessage','id':str(uuid.uuid4()),'text':'offline selected-home result','phase':'final_answer'}]}}})
  continue
 emit({'id':request['id'],'result':result})
'''

FAKE_CLAUDE='''
import json,os,sys
from pathlib import Path
args=sys.argv[1:];resumed='--resume' in args
native=args[args.index('--resume' if resumed else '--session-id')+1]
with Path(__file__).with_suffix('.launches').open('a') as log:
 log.write(json.dumps({'home':os.environ.get('CLAUDE_CONFIG_DIR'),'argv':args,'method':'resume' if resumed else 'start','native':native})+'\\n')
sys.stdin.read()
print(json.dumps({'type':'system','subtype':'init','session_id':native,'model':args[args.index('--model')+1],'tools':['Read']}),flush=True)
print(json.dumps({'type':'result','subtype':'success','session_id':native,'result':'offline selected-home result','num_turns':1,'is_error':False}),flush=True)
'''


class SelectedHomeLaunchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Keep the socket below macOS's Unix-domain path limit, including Group's filename.
        temporary=tempfile.TemporaryDirectory(dir='/tmp');self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve();self.journal=Journal(self.root/'state');self.addCleanup(self.journal.close)
        self.group=None
        self.homes={};self.ambient={};self.later={}
        for provider in ('codex','claude'):
            for mapping,label in [(self.homes,'selected'),(self.ambient,'ambient'),(self.later,'later')]:
                home=self.root/(label+' '+provider);home.mkdir();mapping[provider]=home
                if provider=='codex':
                    (home/'config.toml').write_text('[mcp_servers.'+label+'-server]\ncommand="fixture"\n[plugins.'+label+'-plugin]\nenabled=true\n')
                else:(home/'settings.json').write_text(json.dumps({'enabledPlugins':{label+'-plugin':True}}))
        environment=patch.dict(os.environ,{'PATH':os.defpath,'NPEER_HOME':str(self.journal.root),
            'CODEX_HOME':str(self.ambient['codex']),'CLAUDE_CONFIG_DIR':str(self.ambient['claude'])},clear=True)
        environment.start();self.addCleanup(environment.stop)
        runtime=private_directory(self.root/'run')
        sockets=patch('nativepeers.supervisor.runtime_root',return_value=runtime);sockets.start();self.addCleanup(sockets.stop)
        self.parent=Registry(self.journal).register('codex',self.root,new_id(),'offline parent',identity(os.getpid()).json(),
            str(self.root),{'kind':'fake'},owner_kind='test')
        self.caller=Caller('peer',self.parent['handle'],self.parent['incarnation_id'])

    async def asyncTearDown(self):
        if self.group and hasattr(self.group,'server'):
            await self.group.close()
            await asyncio.gather(*(child['reader'] for child in self.group.children.values()),return_exceptions=True)

    async def completed(self,task_id):
        async with asyncio.timeout(10):
            while True:
                row=self.journal.one('SELECT * FROM tasks WHERE task_id=?',(task_id,))
                if row['state'] not in ('QUEUED','RUNNING'):break
                await asyncio.sleep(.01)
        self.assertEqual(row['state'],'COMPLETED',row)
        result=json.loads((self.journal.root/'artifacts'/row['result_artifact_id']).read_text())
        self.assertEqual(result['summary'],'offline selected-home result')

    def assert_selected_identity(self,worker_id,provider,native_id,generation):
        row=self.journal.one('SELECT * FROM workers WHERE worker_id=?',(worker_id,))
        self.assertEqual(row['generation'],generation);self.assertEqual(row['native_id'],native_id)
        self.assertEqual(json.loads(row['lease_json'])['provider_home'],str(self.homes[provider]))
        peer=Registry(self.journal).get(row['peer_key'])
        self.assertEqual(peer['home'],str(self.homes[provider]))
        self.assertEqual(peer['peer_key']['native_session_id'],native_id)
        binding=json.loads(Path(json.loads(row['lease_json'])['binding']).read_text())
        self.assertEqual(binding['peer_key'],row['peer_key']);self.assertEqual(binding['state'],'BOUND')
        return row['peer_key']

    def assert_selected_profile(self,provider,launch):
        self.assertEqual(launch['home'],str(self.homes[provider]));args=launch['argv']
        if provider=='codex':
            values=[tomllib.loads(args[index+1]) for index,arg in enumerate(args) if arg=='-c']
            policy=next(value['mcp_server_policy'] for value in values if 'mcp_server_policy' in value)
            plugins=next(value['plugins'] for value in values if 'plugins' in value)
            self.assertEqual(policy['selected-server'],{'enabled':False})
            self.assertNotIn('ambient-server',policy);self.assertNotIn('later-server',policy)
        else:plugins=json.loads(args[args.index('--settings')+1])['enabledPlugins']
        self.assertEqual(plugins,{'selected-plugin':False} if provider=='claude' else {'selected-plugin':{'enabled':False}})

    async def exercise(self,provider):
        binary=self.root/(provider+'-fixture.py')
        binary.write_text('#!'+sys.executable+'\n'+(FAKE_CODEX if provider=='codex' else FAKE_CLAUDE));binary.chmod(0o700)
        atomic_json(self.journal.root/'compat/runtime.json',{'providers':{provider:fingerprint(binary)}})
        group_id,worker_id,task_id=[new_id() for _ in range(3)]
        model='gpt-fixture' if provider=='codex' else 'claude-fixture'
        spec=dict(provider=provider,title='selected home fixture',model=model,reasoning_effort='xhigh',cwd=str(self.root),
            permission_profile='analysis',max_task_turns=2,wall_time_seconds=10)
        lease=dict(provider_home=str(self.homes[provider]),spec=spec,executable=str(binary),
            binding=str(self.journal.root/'bindings'/new_id()),parent_key=self.caller.key,expires_at=time.time()+120,
            grants={'worker_id':worker_id,'group_id':group_id,'depth':1})
        policy=dict(workers=[worker_id],initial_tasks=[task_id],completion_mode='wait',followup_grace_seconds=60,
            wall_time_seconds=60,created_at=time.time(),parent_owner=self.parent['owner'],parent_incarnation=self.caller.incarnation,survive_parent=False)
        self.journal.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(group_id,self.caller.key,canonical(policy).decode(),'RESERVED','{}'))
        self.journal.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (worker_id,group_id,None,provider,None if provider=='codex' else new_id(),spec['title'],model,None,'xhigh',str(self.root),None,'RESERVED',1,canonical(lease).decode()))
        self.journal.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(task_id,worker_id,None,None,'QUEUED',None,None,1,canonical({'text':'initial fixture input'}).decode()))
        self.journal.db.execute('INSERT INTO completion_routes VALUES(?,?,?,?,?,?,?,?,?)',(group_id,1,self.caller.key,'wait','UNCLAIMED',None,None,new_id(),1))
        private_directory(self.journal.root/'groups'/group_id)
        self.group=Group(self.journal,group_id);await self.group.initialize()
        await self.completed(task_id)
        first=self.journal.one('SELECT * FROM workers WHERE worker_id=?',(worker_id,));native=first['native_id']
        peer_key=self.assert_selected_identity(worker_id,provider,native,1)
        owner=self.group.children[worker_id]['owner']
        await self.group.release_worker(worker_id);await self.group.children[worker_id]['reader']
        self.assertFalse(matches(owner))
        self.assertEqual(self.journal.one('SELECT state FROM workers WHERE worker_id=?',(worker_id,))['state'],'RELEASED_RESUMABLE')
        os.environ.update(CODEX_HOME=str(self.later['codex']),CLAUDE_CONFIG_DIR=str(self.later['claude']))
        # Only native Claude discovery is replaced: ownership, resume admission, socket control and launches remain real.
        with patch('nativepeers.adapters.metadata.claude_sessions',return_value=[]):
            resumed=await resume_worker(self.journal,self.caller,worker_id,'resumed fixture input','wait')
        await self.completed(resumed['task_id'])
        self.assertEqual(self.assert_selected_identity(worker_id,provider,native,2),peer_key)
        launches=[json.loads(line) for line in binary.with_suffix('.launches').read_text().splitlines()]
        self.assertEqual(len(launches),2)
        self.assertEqual([launch['method'] for launch in launches],['thread/start','thread/resume'] if provider=='codex' else ['start','resume'])
        self.assertEqual([launch['native'] for launch in launches],[native,native])
        for launch in launches:self.assert_selected_profile(provider,launch)

    async def test_codex_selected_home_survives_launch_and_automated_resume(self):
        await self.exercise('codex')

    async def test_claude_selected_home_survives_launch_and_automated_resume(self):
        await self.exercise('claude')
