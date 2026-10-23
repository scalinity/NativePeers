"""Owned worker history policy and cancellation tests; no native model is used."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

from nativepeers.errors import PeerError
from nativepeers.files import canonical
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.worker_process import Worker


class WorkerVisibilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve();self.journal=Journal(self.root/'state');self.addCleanup(self.journal.close)
        self.executable=self.root/'codex';self.executable.write_text('fixture executable')
        self.native=new_id();self.client=AsyncMock();self.client.process.pid=os.getpid()
        self.worker=self.make_worker()
        self.client.start.return_value={'userAgent':'fixture','nativepeersWorkerSource':True}
        async def call(method,params,**kwargs):
            if method=='model/list':return {'data':[{'model':'gpt-fixture','supportedReasoningEfforts':[{'reasoningEffort':'high'}]}]}
            if method in ('thread/start','thread/resume'):return {'thread':{'id':params.get('threadId',self.native),'source':{'custom':'nativepeers-worker'}},'model':'gpt-fixture'}
            if method=='mcpServerStatus/list':return {'data':[{'name':'nativepeers','tools':{'peer_send':{}}}]}
            return {}
        self.client.call.side_effect=call
        self.enterContext(patch('nativepeers.worker_process.verify',return_value={'fixture':True}))
        self.enterContext(patch('nativepeers.worker_process.AppServer',return_value=self.client))
        self.enterContext(patch('nativepeers.worker_process.codex_config',return_value={}))
        self.worker.bind=Mock(side_effect=lambda native_id,**_:setattr(self.worker,'native',native_id))
        self.worker.emit=Mock()

    def make_worker(self,native=None):
        group,worker=new_id(),new_id()
        spec=dict(provider='codex',title='fixture worker',model='gpt-fixture',reasoning_effort='high',
                  cwd=str(self.root),permission_profile='analysis')
        lease=dict(spec=spec,executable=str(self.executable),binding=str(self.journal.root/'bindings'/new_id()),
                   grants={'worker_id':worker,'group_id':group},expires_at=time.time()+60,provider_home=str(self.root/'codex-home'))
        self.journal.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(group,'fixture-parent','{}','RESERVED','{}'))
        self.journal.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (worker,group,None,'codex',native,spec['title'],'gpt-fixture',None,'high',str(self.root),None,'RESERVED',1,canonical(lease).decode()))
        return Worker(self.journal,worker)

    async def test_cancel_during_catalog_does_not_create_history_or_report_ready(self):
        original=self.client.call.side_effect
        async def canceled(method,params,**kwargs):
            if method=='model/list':self.worker.stopping=True
            return await original(method,params,**kwargs)
        self.client.call.side_effect=canceled
        with self.assertRaises(PeerError) as error:await self.worker.prepare()
        self.assertEqual(error.exception.code,'USER_CANCELED')
        methods=[call.args[0] for call in self.client.call.await_args_list]
        self.assertNotIn('thread/start',methods);self.worker.emit.assert_not_called()

    async def test_canceled_worker_does_not_launch_provider(self):
        self.worker.stopping=True
        with self.assertRaises(PeerError) as error:await self.worker.prepare()
        self.assertEqual(error.exception.code,'USER_CANCELED')
        self.client.start.assert_not_awaited()

    async def test_cancel_during_tool_refresh_does_not_report_ready(self):
        original=self.client.call.side_effect
        async def canceled(method,params,**kwargs):
            if method=='mcpServerStatus/list':self.worker.stopping=True
            return await original(method,params,**kwargs)
        self.client.call.side_effect=canceled
        with self.assertRaises(PeerError) as error:await self.worker.prepare()
        self.assertEqual(error.exception.code,'USER_CANCELED')
        self.assertEqual(self.worker.native,self.native)
        self.worker.emit.assert_not_called()

    async def test_new_history_requires_runtime_ack_before_creation(self):
        for acknowledgment in (None,False,'true',1):
            with self.subTest(acknowledgment=acknowledgment):
                self.client.call.reset_mock()
                self.client.start.return_value={'nativepeersWorkerSource':acknowledgment}
                with self.assertRaises(PeerError) as error:await self.worker.prepare()
                self.assertEqual(error.exception.code,'VISIBILITY_UNSUPPORTED')
                self.client.call.assert_not_awaited()
                self.assertIsNone(self.worker.native)
                policy=json.loads(self.journal.one('SELECT lease_json FROM workers WHERE worker_id=?',(self.worker.row['worker_id'],))['lease_json'])['history_visibility']
                self.assertEqual(policy['state'],'UNSUPPORTED');self.assertEqual(policy['desktop_verification'],'NOT_RUN')

    async def test_ignored_source_retains_native_identity_without_input(self):
        original=self.client.call.side_effect
        async def ignored(method,params,**kwargs):
            response=await original(method,params,**kwargs)
            if method=='thread/start':response['thread']['source']='cli'
            return response
        self.client.call.side_effect=ignored
        with self.assertRaises(PeerError) as error:await self.worker.prepare()
        self.assertEqual(error.exception.code,'VISIBILITY_UNACKNOWLEDGED')
        self.assertEqual(self.worker.native,self.native)
        self.assertNotIn('turn/start',[call.args[0] for call in self.client.call.await_args_list])
        self.worker.emit.assert_not_called()

    async def test_creation_is_durable_and_source_survives_exact_resume(self):
        await self.worker.prepare()
        create=next(call for call in self.client.call.await_args_list if call.args[0]=='thread/start')
        self.assertIs(create.args[1]['ephemeral'],False)
        policy=self.worker.lease['history_visibility']
        self.assertEqual(policy['state'],'CLASSIFIED');self.assertTrue(policy['runtime_acknowledged'])
        self.assertEqual(policy['desktop_verification'],'NOT_RUN')
        self.worker.row['native_id']=self.worker.native
        self.worker.lease['released_native']=True;self.client.call.reset_mock()
        await self.worker.prepare()
        resume=next(call for call in self.client.call.await_args_list if call.args[0]=='thread/resume')
        self.assertEqual(resume.args[1]['threadId'],self.native)
        self.assertNotIn('thread/start',[call.args[0] for call in self.client.call.await_args_list])
        self.assertEqual(self.worker.lease['history_visibility']['persisted_source'],{'custom':'nativepeers-worker'})

    async def test_legacy_pinned_history_remains_resumable_with_explicit_limitation(self):
        self.worker.native=self.native;self.worker.row['native_id']=self.native;self.worker.lease['released_native']=True
        self.client.start.return_value={}
        original=self.client.call.side_effect
        async def legacy(method,params,**kwargs):
            response=await original(method,params,**kwargs)
            if method=='thread/resume':response['thread']['source']='cli'
            return response
        self.client.call.side_effect=legacy
        await self.worker.prepare()
        from nativepeers.worker_visibility import server_arguments
        self.assertEqual(server_arguments(self.worker.row,self.worker.lease),[])
        self.assertEqual(self.worker.lease['history_visibility']['state'],'UNSUPPORTED')
        self.assertFalse(self.worker.lease['history_visibility']['runtime_acknowledged'])

    async def test_archived_exact_resume_unarchives_before_resuming(self):
        self.worker.native=self.native;self.worker.row['native_id']=self.native;self.worker.lease['released_native']=True
        self.worker.lease['history_visibility']['persisted_source']={'custom':'nativepeers-worker'}
        with patch('nativepeers.history_cleanup.resume_archive_state',return_value={'archived':True,'checked':True,'native_id':self.native}):
            await self.worker.prepare()
        calls=self.client.call.await_args_list;methods=[call.args[0] for call in calls]
        self.assertLess(methods.index('thread/unarchive'),methods.index('thread/resume'))
        unarchive=next(call for call in calls if call.args[0]=='thread/unarchive')
        self.assertEqual(unarchive.args[1],{'threadId':self.native})
        self.assertNotIn('thread/start',methods)
        self.assertEqual(self.worker.lease['history_visibility']['state'],'CLASSIFIED')
        receipt=self.journal.one("SELECT payload_json FROM events WHERE subject_id=? AND kind='history-unarchived'",(self.worker.row['worker_id'],))
        self.assertFalse(json.loads(receipt['payload_json'])['archived'])

    async def test_uncertain_unarchive_is_not_retried_or_followed_by_resume(self):
        self.worker.native=self.native;self.worker.row['native_id']=self.native;self.worker.lease['released_native']=True
        original=self.client.call.side_effect
        async def disconnected(method,params,**kwargs):
            if method=='thread/unarchive':raise ConnectionError('drain failed after write')
            return await original(method,params,**kwargs)
        self.client.call.side_effect=disconnected
        with patch('nativepeers.history_cleanup.resume_archive_state',return_value={'archived':True,'checked':True,'native_id':self.native}):
            with self.assertRaises(ConnectionError):await self.worker.prepare()
        methods=[call.args[0] for call in self.client.call.await_args_list]
        self.assertEqual(methods.count('thread/unarchive'),1)
        self.assertNotIn('thread/resume',methods);self.assertNotIn('turn/start',methods)

    def test_manual_archived_resume_exposes_exact_unarchive_and_pinned_home(self):
        import shlex
        from nativepeers.recovery import resume_command
        row=dict(self.worker.row,native_id=self.native,lease_json=canonical(self.worker.lease).decode())
        with (patch('nativepeers.recovery.owned_worker',return_value=(row,{})),patch('nativepeers.recovery.assert_released'),
              patch('nativepeers.history_cleanup.resume_archive_state',return_value={'archived':True,'checked':True,'native_id':self.native})):
            result=resume_command(self.journal,None,row['worker_id'])
        self.assertEqual(result['unarchive_argv'],['codex','unarchive',self.native])
        argv=shlex.split(result['command'])
        self.assertEqual(argv.count('CODEX_HOME='+str(self.root/'codex-home')),2)
        self.assertIn('legacy unclassified histories',result['resume_visibility_note'])
        self.assertFalse(result['launched'])
