"""Private socket readers cannot extend a finite supervisor's lifetime."""
import asyncio
import json
import os
from pathlib import Path
import socket
import tempfile
import time
import unittest
from unittest.mock import patch

from nativepeers.artifacts import Artifacts
from nativepeers.delegation import Delegation
from nativepeers.files import canonical
from nativepeers.journal import Journal
from nativepeers.models import CONTROL_RESPONSE,new_id
from nativepeers.process import identity
from nativepeers.registry import Registry
from nativepeers.supervisor import Group


class GroupReplyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.j=Journal(self.root/'state');self.addCleanup(self.j.close)
        parent=Registry(self.j).register('codex',self.root/'home',new_id(),'parent',identity(os.getpid()).json(),str(self.root),{},owner_kind='test')
        self.gid=new_id();policy={'initial_tasks':[],'parent_incarnation':parent['incarnation_id'],'completion_mode':'wait','created_at':time.time()-120,'wall_time_seconds':60}
        self.j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(self.gid,parent['handle'],canonical(policy).decode(),'COMPLETED','{}'))
        for _ in range(4):
            worker,native=new_id(),new_id()
            self.j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(worker,self.gid,None,'codex',native,'fixture','exact','exact',None,str(self.root),None,'RELEASED_RESUMABLE',1,'{}'))
            for _ in range(8):
                task=new_id();policy['initial_tasks'].append(task)
                artifact=Artifacts(self.j).put(canonical({'task_id':task,'status':'completed','summary':'result '*1700,'worker':{'provider':'codex','native_session_id':native,'title':'fixture'}}),worker,[parent['handle']])
                self.j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(task,worker,None,None,'COMPLETED',artifact,None,1,'{}'))
        self.j.db.execute('UPDATE groups SET policy_json=?',(canonical(policy).decode(),))
        self.group=Group(self.j,self.gid);self.handlers=[];self.writers=[];self.clients=[]
        def accept(reader,writer):
            writer.get_extra_info('socket').setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,4096)
            self.writers.append(writer);self.handlers.append(asyncio.create_task(self.group.connection(reader,writer)))
        self.group.server=await asyncio.start_unix_server(accept,path=str(self.group.endpoint),limit=CONTROL_RESPONSE+1)
        self.addAsyncCleanup(self.cleanup_sockets)

    async def cleanup_sockets(self):
        for client in self.clients:client.close()
        for writer in self.writers:
            if not writer.is_closing() or writer.transport.get_write_buffer_size():writer.transport.abort()
        for task in self.handlers:
            if not task.done():task.cancel()
        await asyncio.gather(*self.handlers,return_exceptions=True)
        self.group.server.close();await asyncio.wait_for(self.group.server.wait_closed(),2)
        self.group.endpoint.unlink(missing_ok=True)

    async def request(self):
        client=socket.socket(socket.AF_UNIX);client.setblocking(False);self.clients.append(client)
        loop=asyncio.get_running_loop();await loop.sock_connect(client,str(self.group.endpoint))
        await loop.sock_sendall(client,canonical({'token':self.group.token,'operation':'result','payload':{'caller_key':self.group.parent.key}})+b'\n')
        return client

    async def read(self,client):
        raw=bytearray();loop=asyncio.get_running_loop()
        async with asyncio.timeout(3):
            while not raw.endswith(b'\n'):
                piece=await loop.sock_recv(client,65536)
                if not piece:break
                raw.extend(piece)
        result=json.loads(raw);assert result['ok'] and len(result['data']['results'])==32
        assert len(raw)<CONTROL_RESPONSE
        return result

    async def pressured(self):
        client=await self.request()
        async with asyncio.timeout(3):
            while not self.writers or self.writers[-1].transport.get_write_buffer_size()==0:await asyncio.sleep(.005)
        assert not self.handlers[-1].done()
        return client

    async def test_paused_reader_and_concurrent_reader_cannot_hold_group_close(self):
        await self.pressured()
        await self.read(await self.request())
        await asyncio.wait_for(self.group.close(),2)
        assert not self.group.endpoint.exists()
        owner=json.loads((self.j.root/'groups'/self.gid/'owner.json').read_text())
        assert owner['released']
        assert self.j.one("SELECT * FROM events WHERE kind='supervisor-released'")
        assert all(task.done() for task in self.handlers)

    async def test_reply_deadline_closes_paused_client_without_group_shutdown(self):
        with patch('nativepeers.supervisor.CONTROL_REPLY_SECONDS',.15):
            await self.pressured()
            await asyncio.wait_for(asyncio.gather(*self.handlers),1)
        assert self.writers[0].transport.is_closing()
        await asyncio.wait_for(self.group.close(),2)

    async def test_canceling_a_pressured_handler_aborts_its_writer(self):
        await self.pressured();self.handlers[0].cancel()
        await asyncio.wait_for(asyncio.gather(*self.handlers,return_exceptions=True),1)
        assert self.writers[0].transport.is_closing()
        await asyncio.wait_for(self.group.close(),2)

    async def test_reading_client_gets_complete_result_and_normal_close(self):
        expected=Delegation(self.j,Registry(self.j)).result(self.group.parent,self.gid)
        response=await self.read(await self.request())
        assert response['data']==expected
        await asyncio.wait_for(self.group.close(),2)
