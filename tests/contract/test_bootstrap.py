import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters,stdio_client

from nativepeers.files import atomic_json
from nativepeers.journal import Journal
from nativepeers.process import identity

ROOT=Path(__file__).resolve().parents[2]


class BootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def test_unbound_handshake_and_prompt_mutation_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=Journal(Path(directory));binding=journal.root/'bindings/bootstrap'
            atomic_json(binding,{'state':'RESERVED_UNBOUND','worker_id':'reserved','owner':identity(os.getpid()).json()})
            parameters=StdioServerParameters(command=str(ROOT/'.venv/bin/python'),args=['-m','nativepeers.mcp_server','--binding',str(binding)],cwd=ROOT,env={'NPEER_HOME':directory,'PYTHONPATH':str(ROOT)})
            async with stdio_client(parameters) as (reader,writer):
                async with ClientSession(reader,writer,read_timeout_seconds=3) as session:
                    await session.initialize()
                    tools=await session.list_tools();self.assertEqual([t.name for t in tools.tools],['task_status'])
                    result=await session.call_tool('peer_send',{'target':'anything','text':'no','client_request_id':'x'})
                    self.assertIn('CALLER_NOT_READY',result.content[0].text)
                    atomic_json(binding,{'state':'BOUND','grants':{'can_spawn':False}})
                    await asyncio.sleep(.2)
                    tools=await session.list_tools()
                    self.assertIn('peer_send',[t.name for t in tools.tools]);self.assertNotIn('worker_spawn',[t.name for t in tools.tools])
            journal.close()
