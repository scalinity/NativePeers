"""Compact reads retain failure, phase and omission semantics across long histories."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nativepeers.artifacts import Artifacts
from nativepeers.caller import Caller
from nativepeers.cli import parser,dispatch
from nativepeers.core import Core
from nativepeers.delegation import Delegation
from nativepeers.errors import PeerError
from nativepeers.files import canonical
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.registry import Registry


class CompactResultsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.j=Journal(Path(temp.name));self.addCleanup(self.j.close)
        registry=Registry(self.j);p=registry.register('codex',self.j.root,new_id(),'parent',identity(os.getpid()).json(),str(self.j.root),{},owner_kind='test')
        self.caller=Caller('peer',p['handle'],p['incarnation_id']);self.service=Delegation(self.j,registry)
        self.group,self.worker,self.native=new_id(),new_id(),new_id();self.ids=[]
        self.j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(self.group,self.caller.key,'{}','FAILED','{}'))
        lease=dict(spec=dict(max_task_turns=200),tasks_started=1)
        self.j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(self.worker,self.group,None,'claude',self.native,'review','sonnet','claude-sonnet-5-5',None,str(self.j.root),None,'IDLE_GRACE',1,json.dumps(lease)))

    def add(self,state='COMPLETED',summary='No findings.',error=None,findings=None,model_observed='claude-sonnet-5-5',native_id='current',limitations=None):
        task=new_id();self.ids.append(task)
        record=dict(task_id=task,group_id=self.group,status=state.lower(),summary=summary,error=error,
                    worker=dict(provider='claude',native_session_id=self.native if native_id=='current' else native_id,title='review'),model_requested='sonnet',model_observed=model_observed,
                    findings=findings or [],verification=dict(tests_run=[],limitations=limitations if limitations is not None else ['Tests not run: analysis profile.']),changes={})
        artifact=Artifacts(self.j).put(canonical(record),self.caller.key,media_type='application/json') if state!='RUNNING' else None
        self.j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(task,self.worker,None,None,state,artifact,None,1,'{}'))
        self.policy=dict(initial_tasks=self.ids[:1],phases={str(i+1):[t] for i,t in enumerate(self.ids)})
        self.j.db.execute('UPDATE groups SET policy_json=?',(json.dumps(self.policy),))
        return task

    async def test_task_size_is_bounded_and_cli_mcp_consistent_with_one_hundred_phases(self):
        target=self.add()
        for _ in range(100):self.add(summary='unrelated history')
        before=self.j.all('SELECT * FROM completion_routes')
        with patch('nativepeers.cli.bind_caller',return_value=self.caller):
            cli=await dispatch(parser().parse_args(['result',target,'--compact','--json']),self.j)
        mcp=await Core(self.j).tool(self.caller,'task_result',dict(handle=target,compact=True))
        self.assertEqual(cli,mcp);self.assertEqual(len(cli['tasks']),1)
        frame=dict(schema_version=1,ok=True,data=cli,error=None,request_id=new_id())
        self.assertLessEqual(len(json.dumps(frame).encode()),4096)
        self.assertNotIn(self.ids[-1],json.dumps(cli));self.assertEqual(before,self.j.all('SELECT * FROM completion_routes'))
        self.assertIn('Tests not run',cli['tasks'][0]['limitations'])

    async def test_empty_rejected_review_stays_incomplete_through_compact_rendering(self):
        from nativepeers.worker_report import extract
        raw='Review retained\n```nativepeers-result\n'+json.dumps(dict(findings=[dict(severity='high',summary='F001: defect')],tests_run=[]))+'\n```'
        findings,checks,limits=extract(raw)
        target=self.add(summary=raw,findings=findings,limitations=limits)
        with patch('nativepeers.cli.bind_caller',return_value=self.caller):
            cli=await dispatch(parser().parse_args(['result',target,'--compact','--json']),self.j)
        mcp=await Core(self.j).tool(self.caller,'task_result',dict(handle=target,compact=True))
        self.assertEqual(cli,mcp);self.assertTrue(cli['tasks'][0]['report_incomplete'])
        self.assertEqual(cli['tasks'][0]['findings']['count'],0)
        self.assertEqual(self.service.result(self.caller,target)['results'][0]['summary'],raw)
        clean=self.add(limitations=[])
        self.assertFalse(self.service.result(self.caller,clean,compact=True)['tasks'][0]['report_incomplete'])

    async def test_failed_initial_completed_later_and_running_followup_are_separate(self):
        failed=self.add('FAILED',error=dict(code='BUDGET_EXHAUSTED',message='Native round cap',may_have_received=True,limit=dict(field='limits.max_native_turns_per_task',value=1,observed=1)))
        self.add();active=self.add('RUNNING')
        value=self.service.result(self.caller,self.group,compact=True)
        self.assertEqual(value['state'],'FAILED');self.assertEqual(value['lifecycle']['initial'],{'FAILED':1})
        self.assertEqual(value['lifecycle']['latest'],{'RUNNING':1});self.assertEqual(value['lifecycle']['active'],1)
        one=self.service.result(self.caller,failed,compact=True)['tasks'][0]
        self.assertEqual(one['phase'],1);self.assertEqual(one['failure']['limit']['observed'],1)
        self.assertTrue(one['failure']['may_have_received']);self.assertEqual(one['state'],'FAILED')
        # Concurrent progress reads must not contradict the captured task membership/states.
        from nativepeers.compact import lifecycle
        self.assertEqual(lifecycle(dict(tasks=[dict(task_id=active,state='QUEUED')],progress=dict(active_tasks=1,queued_tasks=0)),self.policy)['queued'],1)
        missing=self.add();artifact=self.j.one('SELECT result_artifact_id FROM tasks WHERE task_id=?',(missing,))['result_artifact_id']
        (self.j.root/'artifacts'/artifact).unlink()
        self.assertIn('unavailable',self.service.result(self.caller,missing,compact=True)['tasks'][0])
        with self.assertRaises(PeerError) as error:self.service.result(self.caller,self.worker,compact=True)
        self.assertEqual(error.exception.details['handle_kind'],'worker');self.assertIn(self.group,error.exception.details['next_action'])
        with self.assertRaises(PeerError) as error:self.service.result(Caller('peer','stranger'),self.worker,compact=True)
        self.assertEqual(error.exception.code,'APPROVAL_BLOCKED')

    async def test_large_unicode_findings_explicitly_reference_full_result(self):
        target=self.add(summary='猫'*15000,findings=[dict(summary='猫'*1000,severity='warning') for _ in range(20)])
        value=self.service.result(self.caller,target,compact=True)
        self.assertLessEqual(len(json.dumps(dict(schema_version=1,ok=True,data=value,error=None,request_id=new_id())).encode()),4096)
        self.assertIn('omissions',value);self.assertTrue(value['tasks'][0]['result_artifact_id'])
        self.assertTrue(self.service.result(self.caller,target)['results'][0]['summary_truncated'])

    async def test_historical_unobserved_model_is_not_replaced_by_a_later_worker_observation(self):
        earlier=self.add('FAILED',model_observed=None,native_id=None)
        self.j.db.execute('UPDATE workers SET native_id=?,model_observed=? WHERE worker_id=?',('later-native','later-model',self.worker))
        self.add(model_observed='later-model',native_id='later-native')
        for view in (self.service.result(self.caller,earlier,compact=True),self.service.status(self.caller,earlier,compact=True)):
            self.assertIsNone(view['tasks'][0]['model_observed']);self.assertIsNone(view['tasks'][0]['native_id'])
            self.assertEqual(view['tasks'][0]['state'],'FAILED');self.assertEqual(view['tasks'][0]['model_requested'],'sonnet')

    async def test_compact_read_reports_a_worker_membership_change_instead_of_crashing(self):
        task=self.add('RUNNING');original=self.j.all;changed=False
        def read(sql,parameters=()):
            nonlocal changed
            rows=original(sql,parameters)
            if not changed and sql.startswith('SELECT worker_id,peer_key,provider'):
                changed=True
                row=self.j.one('SELECT * FROM workers WHERE worker_id=?',(self.worker,));row['worker_id']=new_id()
                self.j.db.execute('INSERT INTO workers VALUES('+','.join('?' for _ in row)+')',tuple(row.values()))
                self.j.db.execute('UPDATE tasks SET worker_id=? WHERE task_id=?',(row['worker_id'],task))
            return rows
        with patch.object(self.j,'all',side_effect=read):value=self.service.result(self.caller,task,compact=True)
        self.assertTrue(changed);self.assertEqual(value['tasks'][0]['task_id'],task)
        self.assertIn('Worker membership changed',value['tasks'][0]['unavailable'])
        self.assertEqual(value['tasks'][0]['state'],'RUNNING')

    async def test_unavailable_and_absent_terminal_records_do_not_borrow_later_worker_identity(self):
        earlier=self.add('FAILED',model_observed=None,native_id=None)
        artifact=self.j.one('SELECT result_artifact_id FROM tasks WHERE task_id=?',(earlier,))['result_artifact_id']
        (self.j.root/'artifacts'/artifact).unlink()
        self.j.db.execute('UPDATE workers SET native_id=?,model_requested=?,model_observed=? WHERE worker_id=?',
                          ('later-native','later-request','later-model',self.worker))
        for view in (self.service.result(self.caller,earlier,compact=True),self.service.status(self.caller,earlier,compact=True)):
            row=view['tasks'][0];self.assertIn('unavailable',row)
            for field in ('provider','native_id','model_requested','model_observed'):self.assertIsNone(row[field])
        canceled=self.add('CANCELED')
        self.j.db.execute('UPDATE tasks SET result_artifact_id=NULL WHERE task_id=?',(canceled,))
        row=self.service.result(self.caller,canceled,compact=True)['tasks'][0]
        for field in ('provider','native_id','model_requested','model_observed'):self.assertIsNone(row[field])

    async def test_pending_task_can_still_name_its_live_worker_selection(self):
        task=self.add('RUNNING')
        row=self.service.result(self.caller,task,compact=True)['tasks'][0]
        self.assertEqual(row['provider'],'claude');self.assertEqual(row['native_id'],self.native)
        self.assertEqual(row['model_requested'],'sonnet');self.assertIsNone(row['model_observed'])

    async def test_completed_task_without_a_record_reports_unavailable_in_cli_and_mcp(self):
        task=self.add()
        self.j.db.execute('UPDATE tasks SET result_artifact_id=NULL WHERE task_id=?',(task,))
        before=self.j.all('SELECT * FROM completion_routes')
        with patch('nativepeers.cli.bind_caller',return_value=self.caller):
            cli=await dispatch(parser().parse_args(['result',task,'--compact','--json']),self.j)
        mcp=await Core(self.j).tool(self.caller,'task_result',dict(handle=task,compact=True))
        self.assertEqual(cli,mcp)
        for view in (cli,self.service.status(self.caller,task,compact=True)):
            row=view['tasks'][0]
            self.assertEqual(row['state'],'COMPLETED')
            self.assertIn('no recorded result artifact',row['unavailable']['reason'])
            self.assertNotIn('findings',row);self.assertNotIn('checks',row)
            for field in ('provider','native_id','model_requested','model_observed'):self.assertIsNone(row[field])
            self.assertLessEqual(len(json.dumps(dict(schema_version=1,ok=True,data=view,error=None,request_id=new_id())).encode()),4096)
        self.assertEqual(before,self.j.all('SELECT * FROM completion_routes'))

    async def test_oversize_retained_identity_uses_an_explicit_bounded_reference(self):
        task=self.add(native_id='猫'*4000,findings=[dict(severity='warning',summary='Inspect full retained finding')])
        value=self.service.result(self.caller,task,compact=True)
        envelope=dict(schema_version=1,ok=True,data=value,error=None,request_id=new_id())
        self.assertLessEqual(len(json.dumps(envelope).encode()),4096)
        row=value['tasks'][0];self.assertTrue(row['details_omitted'])
        self.assertEqual(row['findings_count'],1);self.assertTrue(row['result_artifact_id'])
        self.assertEqual(row['state'],'COMPLETED')
        failed=self.add('FAILED',native_id='猫'*4000,error=dict(code='BUDGET_EXHAUSTED',may_have_received=True))
        missing=self.add();artifact=self.j.one('SELECT result_artifact_id FROM tasks WHERE task_id=?',(missing,))['result_artifact_id']
        (self.j.root/'artifacts'/artifact).unlink()
        value=self.service.result(self.caller,self.group,compact=True)
        self.assertLessEqual(len(json.dumps(dict(data=value,ok=True,schema_version=1,error=None,request_id=new_id())).encode()),4096)
        self.assertEqual(value['tasks'][0]['task_id'],failed)
        self.assertEqual(value['tasks'][0]['failure']['code'],'BUDGET_EXHAUSTED')
        self.assertTrue(value['tasks'][0]['failure']['may_have_received'])
        self.assertTrue(value['tasks'][1]['unavailable'])
