import asyncio
import json
import os
from pathlib import Path
import tempfile
import sys
import time
import unittest
from unittest.mock import patch

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters,stdio_client

from nativepeers.files import atomic_json
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.registry import Registry

ROOT=Path(__file__).resolve().parents[2]


class BootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_caller_facade_uses_its_owned_bootstrap_before_native_id(self):
        from nativepeers.mcp_server import Facade
        with tempfile.TemporaryDirectory() as directory:
            journal=Journal(Path(directory));binding=journal.root/'bindings/bootstrap'
            owner=identity(os.getpid()).json();native=new_id()
            lease=dict(state='RESERVED_UNBOUND',kind='peer',provider='antigravity',worker_id='reserved',owner=owner,
                expires_at=time.time()+60,grants={'worker_id':'reserved','can_spawn':False})
            atomic_json(binding,lease)
            journal.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',('group','parent','{}','RUNNING','{}'))
            journal.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                ('reserved','group',None,'antigravity',None,'fixture',None,None,None,directory,None,'RUNNING',1,'{}'))
            manifest=dict(worker_id='reserved',group_id='group',generation=1,owner=owner,native_process=owner,native_id=None)
            atomic_json(journal.root/'workers/reserved.json',manifest)
            with patch.dict(os.environ,{'NPEER_BOOTSTRAP':str(binding),'NPEER_BINDING':''}):
                facade=Facade(journal,None)
                self.assertIsNone(facade.binding)
                self.assertEqual(facade.binding_state()['state'],'RESERVED_UNBOUND')
                self.assertIn('peer_reply',facade.names());self.assertNotIn('worker_spawn',facade.names())
                peer=Registry(journal).register('antigravity',Path(directory),native,'fixture',owner,directory,
                    {'kind':'owned-worker'},owner_kind='owned-worker-controller')
                journal.db.execute('UPDATE workers SET peer_key=?,native_id=?',(peer['handle'],native))
                atomic_json(journal.root/'workers/reserved.json',dict(manifest,native_id=native))
                atomic_json(binding,dict(lease,state='BOUND',peer_key=peer['handle'],incarnation_id=peer['incarnation_id']))
                self.assertEqual(facade.binding_state()['state'],'BOUND')
            journal.close()

    async def test_unbound_handshake_and_prompt_mutation_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=Journal(Path(directory));binding=journal.root/'bindings/bootstrap'
            atomic_json(binding,{'state':'RESERVED_UNBOUND','worker_id':'reserved','owner':identity(os.getpid()).json()})
            parameters=StdioServerParameters(command=sys.executable,args=['-m','nativepeers.mcp_server','--binding',str(binding)],cwd=ROOT,env={'NPEER_HOME':directory,'PYTHONPATH':str(ROOT)})
            async with stdio_client(parameters) as (reader,writer):
                async with ClientSession(reader,writer,read_timeout_seconds=3) as session:
                    await session.initialize()
                    tools=await session.list_tools();self.assertEqual([t.name for t in tools.tools],['task_status'])
                    result=await session.call_tool('peer_send',{'target':'anything','text':'no','client_request_id':'x'})
                    self.assertIn('CALLER_NOT_READY',result.content[0].text)
                    atomic_json(binding,{'state':'RESERVED_UNBOUND','provider':'claude','grants':{}})
                    tools=await session.list_tools();self.assertIn('peer_send',[t.name for t in tools.tools])
                    result=await session.call_tool('peer_send',{'target':'anything','text':'no','client_request_id':'y'})
                    self.assertIn('CALLER_NOT_READY',result.content[0].text)
                    atomic_json(binding,{'state':'BOUND','grants':{'can_spawn':False}})
                    await asyncio.sleep(.2)
                    tools=await session.list_tools()
                    self.assertIn('peer_send',[t.name for t in tools.tools]);self.assertNotIn('worker_spawn',[t.name for t in tools.tools])
            journal.close()
