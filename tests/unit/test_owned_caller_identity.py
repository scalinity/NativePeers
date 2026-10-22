"""Real native-shaped process trees; the compiled fixtures never run a model."""
import contextlib
import asyncio
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from nativepeers.files import atomic_json, canonical
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.registry import Registry
from nativepeers.worker_process import Worker


ROOT=Path(__file__).resolve().parents[2]
BINARIES={'claude':'.local/bin/claude','codex':'.local/share/codex-mcp-manager/current/bin/codex',
          'antigravity':'.local/bin/agy'}
NATIVE_SOURCE=r'''
#include <unistd.h>
#include <sys/wait.h>
int main(int argc, char **argv) {
    if (argc < 2) return 2;
    pid_t child = fork();
    if (child < 0) return 3;
    if (!child) { execvp(argv[1], argv + 1); _exit(127); }
    int status;
    if (waitpid(child, &status, 0) < 0) return 4;
    return WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
}
'''
PROBE_SOURCE=r'''
import asyncio, json, os, subprocess, sys
from pathlib import Path
from nativepeers.errors import PeerError
from nativepeers.journal import Journal
from nativepeers.process import ancestry

async def mcp_call(request):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    args=['-m','nativepeers.mcp_server']
    args+=['--binding',os.environ['NPEER_BOOTSTRAP']] if request.get('fixed') else ['--native-caller']
    parameters=StdioServerParameters(command=sys.executable,args=args,env=dict(os.environ))
    async with stdio_client(parameters) as (reader,writer):
        async with ClientSession(reader,writer) as session:
            await session.initialize()
            result=await session.call_tool('task_status',{})
            return json.loads(result.content[0].text)

print(json.dumps({'ancestry':[p.json() for p in ancestry()]}),flush=True)
for line in sys.stdin:
    request=json.loads(line)
    if request['surface']=='cli':
        result=subprocess.run([sys.executable,'-m','nativepeers','whoami','--json'],capture_output=True,text=True,timeout=10)
        response=dict(json.loads(result.stdout),exit_code=result.returncode)
    elif request['surface']=='mcp':response=asyncio.run(mcp_call(request))
    else:
        journal=Journal()
        try:
            provider=request['provider'];native=request['native_id']
            if provider=='antigravity':
                from nativepeers.antigravity import observe
                data=observe(journal,{'conversationId':native,'toolCall':{'name':'view_file','args':{}}},'PreToolUse')
            else:
                from nativepeers.hooks import observe
                data=observe(provider,{'session_id':native,'hook_event_name':request.get('event','SessionStart')},journal)
            response={'ok':True,'data':data}
        except PeerError as error:response={'ok':False,'error':error.json()}
        finally:journal.close()
    print(json.dumps(response),flush=True)
'''
STARTUP_SOURCE=r'''
import asyncio, json, os, sys, time
from pathlib import Path
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

MUTATIONS=[('peer_send',{'target':'absent','text':'inactive','client_request_id':'inactive-send'}),
           ('peer_reply',{'message_id':'absent','text':'inactive','client_request_id':'inactive-reply'}),
           ('worker_spawn',{'spec':{'workers':[{'provider':'claude','title':'inactive','task':'never run'}]}}),
           ('task_cancel',{'handle':'absent','reason':'inactive'})]

async def main():
    parameters=StdioServerParameters(command=sys.executable,args=['-m','nativepeers.mcp_server','--native-caller'],env=dict(os.environ))
    async with stdio_client(parameters) as (reader,writer):
        async with ClientSession(reader,writer) as session:
            await session.initialize()
            try:tools={'ok':True,'names':[tool.name for tool in (await session.list_tools()).tools]}
            except Exception as error:tools={'ok':False,'error':str(error)}
            async def call(name,args):return json.loads((await session.call_tool(name,args)).content[0].text)
            initial={'tools':tools,'status':await call('task_status',{}),
                     'mutations':[await call(name,args) for name,args in MUTATIONS]}
            print(json.dumps(initial),flush=True)
            # Worker.claude_turn sends its input only after recording native_process.
            await asyncio.to_thread(sys.stdin.readline)
            manifest=json.loads(Path(os.environ['NPEER_FIXTURE_MANIFEST']).read_text())
            native=os.environ['NPEER_FIXTURE_NATIVE']
            print(json.dumps({'type':'system','subtype':'init','session_id':native,'tools':['Read']}),flush=True)
            deadline=time.monotonic()+5
            while json.loads(Path(os.environ['NPEER_BOOTSTRAP']).read_text())['state']!='BOUND':
                if time.monotonic()>deadline:raise TimeoutError('Worker did not bind the fake native init')
                await asyncio.sleep(.005)
            active={'manifest':manifest,'status':await call('task_status',{})}
            if os.getenv('NPEER_FIXTURE_NESTED'):
                active['mutations']=[await call(name,args) for name,args in MUTATIONS]
            Path(os.environ['NPEER_FIXTURE_REPORT']).write_text(json.dumps(active))
    print(json.dumps({'type':'result','subtype':'success','session_id':native,'result':'harmless bootstrap ordering checked','num_turns':1}),flush=True)

asyncio.run(main())
'''


class OwnedCallerIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler=shutil.which('cc')
        if compiler is None:raise unittest.SkipTest('A C compiler is required for real native ancestry fixtures')
        scratch=ROOT/'evidence/scratch';scratch.mkdir(parents=True,exist_ok=True)
        cls.temporary=tempfile.TemporaryDirectory(prefix='owned-identity-',dir=scratch)
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.home=Path(cls.temporary.name).resolve()
        source=cls.home/'native.c';source.write_text(NATIVE_SOURCE)
        executable=cls.home/'fake-native'
        subprocess.run([compiler,str(source),'-o',str(executable)],check=True,capture_output=True,timeout=30)
        for relative in BINARIES.values():
            binary=cls.home/relative;binary.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(executable,binary)
        cls.probe=cls.home/'probe.py';cls.probe.write_text(PROBE_SOURCE)
        cls.startup_probe=cls.home/'startup.py';cls.startup_probe.write_text(STARTUP_SOURCE)

    def setUp(self):
        self.journal=Journal(self.home/new_id());self.addCleanup(self.journal.close)
        self.worker,self.group,self.native=[new_id() for _ in range(3)]
        self.binding=self.journal.root/'bindings/bootstrap'
        self.owner=identity(os.getpid()).json()
        self.manifest_path=self.journal.root/'workers'/(self.worker+'.json')

    def seed(self,provider,*,reserved=False):
        self.provider=provider
        peer=Registry(self.journal).register(provider,self.home/('.'+provider),self.native,'owned fixture',
            self.owner,self.home,{'kind':'owned-worker'},owner_kind='owned-worker-controller')
        self.peer=peer
        self.lease=dict(state='RESERVED_UNBOUND' if reserved else 'BOUND',kind='peer',provider=provider,
            worker_id=self.worker,owner=self.owner,expires_at=time.time()+120,grants={'worker_id':self.worker,'can_spawn':False})
        if not reserved:self.lease.update(peer_key=peer['handle'],incarnation_id=peer['incarnation_id'])
        atomic_json(self.binding,self.lease)
        self.journal.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(self.group,'fixture-parent','{}','RUNNING','{}'))
        self.journal.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (self.worker,self.group,peer['handle'],provider,None if reserved else self.native,'owned fixture',None,None,None,
             str(self.home),None,'RUNNING',1,canonical({'binding':str(self.binding),'spec':{'permission_profile':'analysis'}}).decode()))
        self.manifest=dict(worker_id=self.worker,group_id=self.group,generation=1,owner=self.owner,
                           native_id=None if reserved else self.native,native_process=None)

    def read(self,process):
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout,selectors.EVENT_READ)
            self.assertTrue(selector.select(15),'Harmless native fixture did not respond')
        line=process.stdout.readline()
        self.assertTrue(line,'Harmless native fixture closed unexpectedly')
        return json.loads(line)

    @contextlib.contextmanager
    def launch(self,providers,*,record=True):
        env=dict(os.environ,HOME=str(self.home),NPEER_HOME=str(self.journal.root),PYTHONPATH=str(ROOT),
                 NPEER_BINDING='',NPEER_BOOTSTRAP=str(self.binding),CODEX_THREAD_ID='')
        command=[str(self.home/BINARIES[p]) for p in providers]+[sys.executable,str(self.probe)]
        process=subprocess.Popen(command,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                 text=True,start_new_session=True)
        try:
            owners=self.read(process)['ancestry']
            outer=[p for p in owners if p['executable']==str(self.home/BINARIES[providers[0]])][-1]
            if record:
                self.manifest['native_process']=outer;atomic_json(self.manifest_path,self.manifest)
            yield process,outer
        finally:
            process.stdin.close()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                import signal
                os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)
            error=process.stderr.read();process.stdout.close();process.stderr.close()
            self.assertEqual(process.returncode,0,error)

    def call(self,process,surface,**args):
        process.stdin.write(json.dumps(dict(surface=surface,**args))+'\n');process.stdin.flush()
        return self.read(process)

    def assert_unbound(self,response):
        self.assertFalse(response['ok'],response)
        self.assertEqual(response['error']['code'],'CALLER_UNBOUND',response)

    def test_one_and_two_nested_layers_cannot_borrow_bootstrap_via_cli_or_mcp(self):
        self.seed('claude')
        for descendants in [('codex',),('codex','antigravity'),('claude',),('claude','claude')]:
            with self.subTest(descendants=descendants),self.launch(('claude',)+descendants) as (process,_):
                for surface in ('cli','mcp'):
                    response=self.call(process,surface);self.assert_unbound(response)
                    if surface=='cli':self.assertEqual(response['exit_code'],4)

    def test_current_owner_cli_mcp_and_explicit_fixed_binding_remain_supported(self):
        self.seed('claude')
        with self.launch(('claude',)) as (process,_):
            cli=self.call(process,'cli');self.assertTrue(cli['ok'],cli)
            self.assertEqual(cli['data']['key'],self.peer['handle'])
            mcp=self.call(process,'mcp');self.assertTrue(mcp['ok'],mcp)
            self.assertEqual(mcp['data']['native_session_id'],self.native)
        with self.launch(('claude','codex')) as (process,_):
            explicit=self.call(process,'mcp',fixed=True);self.assertTrue(explicit['ok'],explicit)
            self.assertEqual(explicit['data']['peer_key'],self.peer['handle'])

    def test_owned_hooks_reject_nested_provider_and_uuid_mismatches(self):
        for provider in BINARIES:
            # Each provider gets its own exact current worker row and manifest.
            if hasattr(self,'provider'):
                self.journal.db.execute('DELETE FROM workers');self.journal.db.execute('DELETE FROM groups')
            self.seed(provider)
            with self.subTest(provider=provider),self.launch((provider,)) as (process,_):
                self.assertTrue(self.call(process,'hook',provider=provider,native_id=self.native)['ok'])
                count=len(self.journal.all("SELECT * FROM events WHERE kind='native-hook-observed'"))
                self.assert_unbound(self.call(process,'hook',provider=provider,native_id=new_id()))
                self.assertEqual(len(self.journal.all("SELECT * FROM events WHERE kind='native-hook-observed'")),count)
            for descendants in [(provider,),('claude' if provider!='claude' else 'codex','antigravity')]:
                with self.subTest(provider=provider,descendants=descendants),self.launch((provider,)+descendants) as (process,_):
                    self.assert_unbound(self.call(process,'hook',provider=provider,native_id=self.native))
        self.journal.db.execute("UPDATE workers SET provider='codex' WHERE worker_id=?",(self.worker,))
        with self.launch(('antigravity',)) as (process,_):
            self.assert_unbound(self.call(process,'hook',provider='antigravity',native_id=self.native))

    def test_bootstrap_before_native_id_and_next_serialized_turn(self):
        for provider in BINARIES:
            if hasattr(self,'provider'):
                self.journal.db.execute('DELETE FROM workers');self.journal.db.execute('DELETE FROM groups')
            self.seed(provider,reserved=True)
            with self.subTest(provider=provider),self.launch((provider,)) as (process,first):
                self.assertTrue(self.call(process,'hook',provider=provider,native_id=self.native)['ok'])
                reservation=self.call(process,'mcp');self.assertTrue(reservation['ok'],reservation)
                self.assertEqual(reservation['data']['state'],'RESERVED_UNBOUND')
                self.assertIsNone(self.journal.one('SELECT native_id FROM workers')['native_id'])
                self.lease.update(state='BOUND',peer_key=self.peer['handle'],incarnation_id=self.peer['incarnation_id'])
                atomic_json(self.binding,self.lease)
                self.journal.db.execute('UPDATE workers SET native_id=? WHERE worker_id=?',(self.native,self.worker))
                self.manifest['native_id']=self.native;atomic_json(self.manifest_path,self.manifest)
            with self.launch((provider,)) as (process,second):
                self.assertNotEqual(first['pid'],second['pid'])
                for surface in ('cli','mcp'):
                    response=self.call(process,surface);self.assertTrue(response['ok'],response)
                for event in ('SessionStart','PreToolUse','SessionEnd'):
                    self.assertTrue(self.call(process,'hook',provider=provider,native_id=self.native,event=event)['ok'])
                self.assertEqual(Registry(self.journal).get(self.peer['handle'])['incarnation_id'],self.peer['incarnation_id'])

    def test_missing_stale_and_wrong_process_manifests_do_not_bind(self):
        self.seed('claude')
        with self.launch(('claude',)) as (process,_):
            correct=dict(self.manifest)
            for override in [None,{'generation':2},{'worker_id':new_id()},{'native_id':new_id()},
                             {'owner':dict(self.owner,start='stale')},{'native_process':None},
                             {'native_process':dict(correct['native_process'],start='stale')}]:
                with self.subTest(override=override):
                    if override is None:self.manifest_path.unlink(missing_ok=True)
                    else:atomic_json(self.manifest_path,dict(correct,**override))
                    for surface in ('cli','mcp','hook'):
                        self.assert_unbound(self.call(process,surface,provider='claude',native_id=self.native))

    def test_mcp_initializes_before_spawn_returns_without_activating_inherited_binding(self):
        async def exercise(nested):
            self.seed('claude',reserved=True)
            lease=dict(binding=str(self.binding),provider_home=str(self.home/'.claude'),
                executable=str(self.home/BINARIES['claude']),expires_at=self.lease['expires_at'],
                grants=self.lease['grants'],spec={'permission_profile':'analysis'})
            self.journal.db.execute('UPDATE workers SET lease_json=? WHERE worker_id=?',(canonical(lease).decode(),self.worker))
            # Claude reserves its UUID before launching; its native PID is unknown.
            self.journal.db.execute('UPDATE workers SET native_id=? WHERE worker_id=?',(self.native,self.worker))
            worker=Worker(self.journal,self.worker);worker.bootstrap()
            worker.tasks_started=1;worker.running_task=new_id()
            before={table:self.journal.all('SELECT * FROM '+table) for table in
                    ('workers','peer_instances','messages','events','spawn_requests','send_requests','tasks','attempts')}
            report=self.journal.root/'startup-report.json'
            environment=dict(os.environ,HOME=str(self.home),PYTHONPATH=str(ROOT),NPEER_HOME=str(self.journal.root),
                NPEER_BINDING='',NPEER_BOOTSTRAP=str(self.binding),NPEER_FIXTURE_MANIFEST=str(self.manifest_path),
                NPEER_FIXTURE_NATIVE=self.native,NPEER_FIXTURE_REPORT=str(report),CODEX_THREAD_ID='')
            if nested:environment['NPEER_FIXTURE_NESTED']='1'
            arguments=([str(self.home/BINARIES['codex'])] if nested else [])+[sys.executable,str(self.startup_probe)]
            spawn=asyncio.create_subprocess_exec;process=None;initial=None
            async def delayed_spawn(*args,**kwargs):
                nonlocal process,initial
                process=await spawn(str(self.home/BINARIES['claude']),*args[1:],**kwargs)
                # Hold the actual worker's create_subprocess_exec await until the
                # child has initialized MCP and requested tools in another process.
                initial=json.loads(await asyncio.wait_for(process.stdout.readline(),10))
                self.assertIsNone(worker.provider_identity)
                self.assertIsNone(json.loads(self.manifest_path.read_text())['native_process'])
                self.assertEqual({table:self.journal.all('SELECT * FROM '+table) for table in before},before)
                return process
            try:
                with patch('nativepeers.worker_process.claude_arguments',return_value=arguments), \
                     patch('nativepeers.worker_process.launch_environment',return_value=environment), \
                     patch('nativepeers.worker_process.asyncio.create_subprocess_exec',side_effect=delayed_spawn):
                    result=await asyncio.wait_for(worker.claude_turn('no model input'),15)
                self.assertEqual(result['status'],'completed',result)
                self.assertTrue(initial['tools']['ok'],initial)
                self.assertIn('peer_send',initial['tools']['names'])
                self.assertEqual(initial['status']['data']['state'],'RESERVED_UNBOUND')
                for response in initial['mutations']:
                    self.assertFalse(response['ok'],response)
                    self.assertEqual(response['error']['code'],'CALLER_NOT_READY',response)
                active=json.loads(report.read_text())
                self.assertEqual(active['manifest']['native_process'],worker.provider_identity)
                if nested:
                    for response in [active['status'],*active['mutations']]:self.assert_unbound(response)
                else:
                    self.assertTrue(active['status']['ok'],active)
                    self.assertEqual(active['status']['data']['native_session_id'],self.native)
                for table in ('messages','send_requests','spawn_requests','attempts','tasks'):
                    self.assertEqual(self.journal.all('SELECT * FROM '+table),[])
            finally:
                if process and process.returncode is None:
                    process.stdin.close()
                    try:await asyncio.wait_for(process.communicate(),5)
                    except asyncio.TimeoutError:
                        import signal
                        os.killpg(process.pid,signal.SIGKILL);await process.wait()
        for nested in (False,True):
            with self.subTest(nested=nested):
                if hasattr(self,'provider'):
                    self.journal.db.execute('DELETE FROM workers');self.journal.db.execute('DELETE FROM groups')
                asyncio.run(exercise(nested))
