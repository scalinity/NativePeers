"""Public spawn through real controllers editing only explicitly selected copies."""
import asyncio,json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
from nativepeers.artifacts import Artifacts
from nativepeers.caller import Caller
from nativepeers.compatibility import fingerprint
from nativepeers.delegation import Delegation
from nativepeers.errors import PeerError
from nativepeers.files import atomic_json
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity,matches
from nativepeers.registry import Registry
from nativepeers.supervisor import Group
from nativepeers.worktrees import prepare

FAKE='''
import json,sys,time
from pathlib import Path
args=sys.argv[1:];native=args[args.index('--session-id')+1];sys.stdin.read()
print(json.dumps({'type':'system','subtype':'init','session_id':native,'model':args[args.index('--model')+1],'tools':['Read','Write']}),flush=True)
time.sleep(.1)
assert Path('main.py').read_text()=='VALUE = 1\\n'
Path('main.py').write_text('VALUE = 42\\n');Path('created.txt').write_text('owned edit\\n')
print(json.dumps({'type':'result','subtype':'success','session_id':native,'result':'edited selected copy, VALUE=42','num_turns':1,'is_error':False}),flush=True)
'''


class CopySpawnTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_spawn_result_patches_preserve_non_git_source_and_select_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();home=root/'home';source=root/'source';source.mkdir();(source/'main.py').write_text('VALUE = 1\n');(source/'unselected').write_text('keep\n')
            binary=home/'.local/bin/claude';binary.parent.mkdir(parents=True);binary.write_text('#!'+sys.executable+'\n'+FAKE);binary.chmod(0o700)
            journal=Journal(root/'state');self.addCleanup(journal.close);registry=Registry(journal)
            atomic_json(journal.root/'compat/runtime.json',{'providers':{'claude':fingerprint(binary)}})
            parent=registry.register('codex',home,new_id(),'copy parent',identity(os.getpid()).json(),str(source),{'kind':'fake'},owner_kind='test',policy={'profiles':['workspace']})
            caller=Caller('peer',parent['handle'],parent['incarnation_id']);delegation=Delegation(journal,registry);groups=[]
            async def start(group_id):
                group=Group(journal,group_id);groups.append(group);await group.initialize()
            spec={'client_request_id':'copy-fixture','workers':[{'provider':'claude','title':str(i),'task':'edit only selected input','model':'claude-fixture','permission_profile':'workspace','workspace':{'mode':'isolated-copy','file_allowlist':['main.py']}} for i in range(2)],'completion_mode':'wait','limits':{'max_concurrent_workers':2,'wall_time_seconds':5,'followup_grace_seconds':0}}
            with patch.dict(os.environ,{'NPEER_HOME':str(journal.root)}),patch('nativepeers.delegation.Path.home',return_value=home),patch.object(delegation,'ensure_supervisor',side_effect=start):
                try:
                    status=await delegation.spawn(caller,spec)
                    await asyncio.wait_for(groups[0].monitor(),10)
                    await groups[0].close()
                    value=await delegation.wait(caller,status['group_id'])
                finally:
                    for group in groups:await group.close()
            self.assertTrue(value['ready']);results=value['result']['results'];self.assertEqual(len(results),2)
            self.assertTrue(all(r['status']=='completed' for r in results),results)
            self.assertEqual(len({r['worker']['native_session_id'] for r in results}),2)
            for result in results:
                changes=result['changes'];work=Path(changes['worktree'])
                self.assertEqual(work.parent,source/'.worktrees');self.assertFalse((work/'unselected').exists())
                self.assertFalse(changes['baseline']['git']);self.assertEqual(changes['baseline']['workspace_mode'],'isolated-copy')
                self.assertEqual((work/'main.py').read_text(),'VALUE = 42\n')
                import base64
                for name in ('patch_artifact','worker_delta_artifact'):
                    patch_bytes=base64.b64decode(Artifacts(journal).read(changes[name],caller.key)['data_base64'])
                    self.assertIn(b'+VALUE = 42',patch_bytes);self.assertIn(b'+owned edit',patch_bytes)
                event=journal.one("SELECT payload_json FROM events WHERE subject_id=? AND kind='worker-delta-artifact'",(changes['worker_delta_artifact'],))
                self.assertEqual(json.loads(event['payload_json'])['input_tree'],changes['worker_delta_base'])
            self.assertEqual((source/'main.py').read_text(),'VALUE = 1\n');self.assertEqual((source/'unselected').read_text(),'keep\n')
            self.assertFalse((source/'.git').exists());self.assertTrue(all(not matches(c['owner']) for c in groups[0].children.values()))

    async def test_changed_copy_selection_rejects_batch_before_native_start(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();source=root/'source';source.mkdir();(source/'input').write_text('one')
            home=root/'home';binary=home/'.local/bin/claude';binary.parent.mkdir(parents=True);binary.write_text('not launched')
            journal=Journal(root/'state');self.addCleanup(journal.close);registry=Registry(journal)
            parent=registry.register('codex',home,new_id(),'parent',identity(os.getpid()).json(),str(source),{'kind':'fake'},owner_kind='test',policy={'profiles':['workspace']})
            delegation=Delegation(journal,registry);caller=Caller('peer',parent['handle'],parent['incarnation_id'])
            def mutate(*args,**kwargs):
                value=prepare(*args,**kwargs);(source/'input').write_text('two');return value
            spec={'workers':[{'provider':'claude','title':str(i),'task':'fixture','permission_profile':'workspace','workspace':{'mode':'isolated-copy','file_allowlist':['input']}} for i in range(2)]}
            with patch('nativepeers.delegation.Path.home',return_value=home),patch('nativepeers.delegation.prepare',side_effect=mutate),patch.object(delegation,'ensure_supervisor',new_callable=AsyncMock) as start:
                with self.assertRaises(PeerError):await delegation.spawn(caller,spec)
                start.assert_not_awaited()
            self.assertEqual((source/'input').read_text(),'two');self.assertFalse((source/'.git').exists())
