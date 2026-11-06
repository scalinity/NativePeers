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

    async def test_complete_report_pages_bytes_before_decoding_a_split_multibyte_character(self):
        import hashlib
        from nativepeers.models import RESULT_PAGE
        task=self.add(summary='BOUNDARY_SENTINEL')
        old=self.j.one('SELECT result_artifact_id FROM tasks WHERE task_id=?',(task,))['result_artifact_id']
        record=json.loads((self.j.root/'artifacts'/old).read_bytes())
        start=canonical(record).index(b'BOUNDARY_SENTINEL')
        text='START-'+'x'*(RESULT_PAGE-1-start-len('START-'))+'猫\nMIDDLE-Δ\n'+'y'*19000+'\nEND-π'
        record['summary']=text;raw=canonical(record)
        self.assertEqual(raw[RESULT_PAGE-1:RESULT_PAGE+2],'猫'.encode())
        artifact=Artifacts(self.j).put(raw,self.caller.key,media_type='application/json')
        self.j.db.execute('UPDATE tasks SET result_artifact_id=? WHERE task_id=?',(artifact,task))
        offsets=[];read=Artifacts.read
        def page(store,handle,reader,offset=0,limit=RESULT_PAGE,**kwargs):
            offsets.append(offset);return read(store,handle,reader,offset,limit,**kwargs)
        with patch.object(Artifacts,'read',page),patch('nativepeers.cli.bind_caller',return_value=self.caller):
            result=await dispatch(parser().parse_args(['result',task,'--report']),self.j)
        self.assertEqual(result['report'],text)
        self.assertEqual(result['sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(result['byte_size'],len(raw));self.assertEqual(offsets,[0,RESULT_PAGE])

    async def test_truncated_report_previews_end_on_a_line_with_exact_recovery_command(self):
        import re
        text=('A complete line with multibyte text 猫. '*60+'\n')*40
        task=self.add(summary=text)
        normal=self.service.result(self.caller,task)['results'][0]
        prefix,marker=normal['summary'].rsplit('\n',1)
        self.assertTrue(text.startswith(prefix+'\n'))
        self.assertLessEqual(len(normal['summary'].encode()),12288)
        self.assertTrue(normal['summary_truncated'])
        omitted=int(re.search(r'(\d+) bytes omitted',marker).group(1))
        self.assertEqual(omitted,len(text.encode())-len(prefix.encode()))
        command='npeer result '+task+' --report'
        self.assertIn(command,marker)
        compact=self.service.result(self.caller,task,compact=True)
        self.assertEqual(compact['next_action'],command)
        self.assertEqual(compact['tasks'][0]['report_command'],command)
        self.assertIn(command,compact['tasks'][0]['summary'])
        self.assertTrue(compact['tasks'][0]['summary_truncated'])
        single=self.add(summary='x'*20000)
        self.assertTrue(self.service.result(self.caller,single)['results'][0]['summary'].startswith('[Report truncated: 20000 bytes omitted.'))

    async def test_report_integrity_and_scope_fail_before_returning_any_text(self):
        task=self.add(summary='private complete report\n'*1600)
        with self.assertRaises(PeerError) as denied:self.service.report(Caller('peer','another-parent'),task)
        self.assertEqual(denied.exception.code,'APPROVAL_BLOCKED')
        artifact=self.j.one('SELECT result_artifact_id FROM tasks WHERE task_id=?',(task,))['result_artifact_id']
        path=self.j.root/'artifacts'/artifact;raw=path.read_bytes();path.write_bytes(raw[:-1]+b'!')
        with self.assertRaises(PeerError) as corrupt:self.service.report(self.caller,task)
        self.assertEqual(corrupt.exception.code,'PROTOCOL_ERROR');self.assertIn('content changed',corrupt.exception.message)

    async def test_report_requires_one_recorded_task_and_rejects_mixed_views(self):
        task=self.add()
        with self.assertRaises(PeerError):self.service.report(self.caller,self.group)
        missing=self.add('RUNNING')
        with self.assertRaises(PeerError) as pending:self.service.report(self.caller,missing)
        self.assertIn('no recorded report',pending.exception.message)
        for flag in ['--compact','--include-artifacts']:
            with patch('nativepeers.cli.bind_caller',return_value=self.caller):
                with self.assertRaises(PeerError):await dispatch(parser().parse_args(['result',task,'--report',flag]),self.j)

    async def test_worker_report_reads_use_the_existing_artifact_reader_grant(self):
        task=self.add(summary='Granted full text 猫\n'*1500)
        artifact=self.j.one('SELECT result_artifact_id FROM tasks WHERE task_id=?',(task,))['result_artifact_id']
        worker=Caller('peer','worker-peer',grants={'worker_id':self.worker,'group_id':self.group})
        with self.assertRaises(PeerError) as denied:self.service.report(worker,task)
        self.assertEqual(denied.exception.code,'APPROVAL_BLOCKED')
        Artifacts(self.j).grant(artifact,self.caller.key,[self.worker])
        self.assertEqual(self.service.report(worker,task)['report'],'Granted full text 猫\n'*1500)

    def test_plain_report_cli_emits_only_the_complete_text(self):
        import contextlib
        import io
        from nativepeers.cli import main
        text='START\n'+'猫 report\n'*2200+'END-π'
        task=self.add(summary=text);output=io.StringIO()
        with patch('sys.argv',['npeer','result',task,'--report']),patch('nativepeers.cli.Journal',return_value=self.j),patch('nativepeers.cli.bind_caller',return_value=self.caller),contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as done:main()
        self.assertEqual(done.exception.code,0);self.assertEqual(output.getvalue(),text)
