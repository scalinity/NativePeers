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
            if method=='config/read':return {'config':{'features':{'apps':False,'plugins':False}}}
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

    async def test_foreign_server_starting_without_tools_is_refused_before_input(self):
        original=self.client.call.side_effect
        async def late(method,params,**kwargs):
            response=await original(method,params,**kwargs)
            if method=='mcpServerStatus/list':
                response['data'].append({'name':'late-account-server','tools':{},'runtimeStatus':'starting'})
            return response
        self.client.call.side_effect=late
        with self.assertRaises(PeerError) as caught:await self.worker.prepare()
        self.assertEqual(caught.exception.code,'PERMISSION_CAPABILITY_UNKNOWN')
        self.assertEqual(caught.exception.details['servers'],['late-account-server'])
        self.assertFalse(caught.exception.may_have_received)
        self.assertIn('disable the named server',str(caught.exception).lower())
        self.assertNotIn('turn/start',[c.args[0] for c in self.client.call.await_args_list])

    async def test_late_tools_after_ready_are_refused_on_every_input(self):
        for resumed in (False,True):
            with self.subTest(resumed=resumed):
                self.worker=self.make_worker(self.native if resumed else None)
                if resumed:self.worker.lease['released_native']=True
                original=self.client.call.side_effect
                await self.worker.prepare()
                async def late(method,params,**kwargs):
                    response=await original(method,params,**kwargs)
                    if method=='mcpServerStatus/list':
                        response['data'].append({'name':'late-account-server','tools':{'unscoped':{}},'runtimeStatus':'connected'})
                    return response
                self.client.call.side_effect=late;self.client.call.reset_mock()
                with self.assertRaises(PeerError) as caught:await self.worker.codex_turn('must not be submitted')
                self.assertEqual(caught.exception.code,'PERMISSION_CAPABILITY_UNKNOWN')
                self.assertFalse(self.worker.input_submitted)
                self.assertEqual(caught.exception.details['stage'],'task-input')
                self.assertNotIn('turn/start',[c.args[0] for c in self.client.call.await_args_list])
                self.client.call.side_effect=original

    async def test_inventory_pages_cannot_hide_a_starting_foreign_server(self):
        original=self.client.call.side_effect
        async def pages(method,params,**kwargs):
            if method=='mcpServerStatus/list':
                if params.get('cursor')=='second':return {'data':[{'name':'late-server','tools':{},'runtimeStatus':'starting'}]}
                return {'data':[{'name':'nativepeers','tools':{'peer_send':{}}}],'nextCursor':'second'}
            return await original(method,params,**kwargs)
        self.client.call.side_effect=pages
        with self.assertRaises(PeerError) as caught:await self.worker.prepare()
        self.assertEqual(caught.exception.details['servers'],['late-server'])

    async def test_start_and_resume_record_the_same_effective_servers(self):
        await self.worker.prepare()
        self.worker.lease['released_native']=True
        await self.worker.prepare()
        rows=self.journal.all("SELECT payload_json FROM events WHERE subject_id=? AND kind='effective-tool-profile' ORDER BY sequence",(self.worker.row['worker_id'],))
        first,second=[json.loads(r['payload_json']) for r in rows]
        self.assertEqual([first['stage'],second['stage']],['start','resume'])
        self.assertEqual(first['servers'],second['servers'])
        self.assertFalse(first['apps_enabled']);self.assertFalse(second['plugins_enabled'])

    async def test_missing_or_enabled_feature_readback_is_refused(self):
        self.worker.client=self.client
        original=self.client.call.side_effect
        for features in ({},{'apps':False,'plugins':True},{'apps':False,'plugins':False}):
            async def readback(method,params,**kwargs):
                if method=='config/read':return {'config':{'features':features}}
                return await original(method,params,**kwargs)
            self.client.call.side_effect=readback
            if all(features.get(k) is False for k in ('apps','plugins')):
                await self.worker.codex_tool_profile('test-input')
            else:
                with self.assertRaises(PeerError) as caught:await self.worker.codex_tool_profile('test-input')
                self.assertEqual(caught.exception.code,'PERMISSION_CAPABILITY_UNKNOWN')
                self.assertEqual(caught.exception.details['features'],{k:features.get(k) for k in ('apps','plugins')})

    async def test_launch_flags_are_used_for_start_and_resume(self):
        from nativepeers.profiles import codex_config
        for resumed in (False,True):
            self.worker=self.make_worker(self.native if resumed else None)
            self.worker.lease['released_native']=resumed
            with patch('nativepeers.worker_process.codex_config',side_effect=codex_config),patch('nativepeers.worker_process.AppServer',return_value=self.client) as construct:
                await self.worker.prepare()
            argv=construct.call_args.args[1]
            for flag in ('features.apps=false','features.plugins=false'):
                self.assertIn(flag,argv);self.assertEqual(argv[argv.index(flag)-1],'-c')

    async def test_repeated_cursor_and_unfinished_inventory_are_refused(self):
        self.worker.client=self.client
        from types import SimpleNamespace
        original=self.client.call.side_effect
        for repeats in (True,False):
            clock=[0]
            async def pages(method,params,**kwargs):
                if method=='mcpServerStatus/list':
                    if not repeats:clock[0]=6
                    return {'data':[],'nextCursor':'same'}
                return await original(method,params,**kwargs)
            self.client.call.side_effect=pages
            with patch('nativepeers.worker_process.time',SimpleNamespace(monotonic=lambda:clock[0])):
                with self.assertRaises(PeerError) as caught:await self.worker.codex_tool_profile('task-input')
            self.assertEqual(caught.exception.code,'PERMISSION_CAPABILITY_UNKNOWN')

    async def test_late_server_task_record_is_definitely_unsubmitted(self):
        await self.worker.prepare()
        self.worker.lease['parent_key']='fixture-parent'
        task=new_id();self.journal.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(task,self.worker.row['worker_id'],None,None,'QUEUED',None,None,1,'{}'))
        original=self.client.call.side_effect
        async def late(method,params,**kwargs):
            response=await original(method,params,**kwargs)
            if method=='mcpServerStatus/list':response['data'].append({'name':'late','tools':{'unscoped':{}},'runtimeStatus':'connected'})
            return response
        self.client.call.side_effect=late;self.worker.close=AsyncMock()
        with patch('nativepeers.usage.service.Usage') as usage:
            usage.return_value.refresh=AsyncMock();usage.return_value.guard.return_value={}
            await self.worker.task(task,'must not submit')
        row=self.journal.one('SELECT * FROM tasks WHERE task_id=?',(task,))
        record=json.loads((self.journal.root/'artifacts'/row['result_artifact_id']).read_text())
        self.assertEqual(row['state'],'FAILED');self.assertFalse(record['input_submitted'])
        self.assertFalse(record['error']['may_have_received']);self.assertFalse(record['fallback_eligibility']['definitive_no_execution'])
        self.assertTrue(self.worker.retire_after_task)
        self.assertNotIn('turn/start',[c.args[0] for c in self.client.call.await_args_list])

    async def test_unsafe_or_unreadable_inventory_refuses_steer_before_submission(self):
        self.worker.client=self.client;self.worker.running_task='task';self.worker.turn='turn'
        self.worker.codex_tool_profile=AsyncMock(side_effect=PeerError('PERMISSION_CAPABILITY_UNKNOWN','unsafe'))
        await self.worker.steer(dict(task_id='task',request_id='request',text='must not steer',input={}))
        outcome=self.worker.emit.call_args.kwargs
        self.assertFalse(outcome['steered']);self.assertFalse(outcome.get('uncertain',False))
        self.assertEqual(outcome['error']['code'],'STEER_REFUSED')
        self.assertNotIn('turn/steer',[c.args[0] for c in self.client.call.await_args_list])

    async def test_turn_changes_during_inventory_cannot_steer_the_successor(self):
        self.worker.client=self.client
        for change_task in (False,True):
            self.worker.running_task='task';self.worker.turn='turn-1'
            async def moved(stage):
                self.worker.turn='turn-2'
                if change_task:self.worker.running_task='next-task'
            self.worker.codex_tool_profile=moved
            await self.worker.steer(dict(task_id='task',request_id='request',text='must not reach successor',input={}))
            outcome=self.worker.emit.call_args.kwargs
            self.assertFalse(outcome['steered']);self.assertFalse(outcome.get('uncertain',False))
            self.assertEqual(outcome['error']['code'],'STEER_REFUSED')
        self.assertNotIn('turn/steer',[c.args[0] for c in self.client.call.await_args_list])

    async def test_inventory_timeout_is_definite_before_the_supervisor_deadline(self):
        self.worker.client=self.client;self.worker.running_task='task';self.worker.turn='turn'
        finished=asyncio.Event()
        async def blocked(stage):
            try:await asyncio.Event().wait()
            finally:finished.set()
        self.worker.codex_tool_profile=blocked
        await asyncio.wait_for(self.worker.steer(dict(task_id='task',request_id='request',text='must not steer',input={})),5)
        outcome=self.worker.emit.call_args.kwargs
        self.assertFalse(outcome['steered']);self.assertFalse(outcome.get('uncertain',False))
        self.assertEqual(outcome['error']['code'],'STEER_REFUSED');self.assertTrue(finished.is_set())
        self.assertNotIn('turn/steer',[c.args[0] for c in self.client.call.await_args_list])
