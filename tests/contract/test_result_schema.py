import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from jsonschema import Draft202012Validator, ValidationError

from nativepeers.artifacts import Artifacts
from nativepeers.files import canonical
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.worker_process import Worker
from nativepeers.worker_report import extract
from nativepeers.worktrees import git, prepare


ROOT=Path(__file__).resolve().parents[2]


class ResultSchemaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.schema=json.loads((ROOT/'schemas/result.v1.json').read_text())
        Draft202012Validator.check_schema(self.schema)
        self.validator=Draft202012Validator(self.schema)

    def legacy_result(self):
        return dict(schema_version=1,group_id=new_id(),task_id=new_id(),
                    worker={'provider':'claude','native_session_id':new_id(),'title':'fixture'},
                    status='completed',summary='retained text',model_requested='sonnet',model_observed=None,
                    native_turn_ids=[],completed_at=0)

    def test_optional_fields_preserve_older_results_and_validate_report_provenance(self):
        legacy=self.legacy_result();self.validator.validate(legacy)
        findings,checks,_=extract('```nativepeers-result\n'+json.dumps({
            'findings':[{'severity':'warning','summary':'fixture','file':'base.py','line':1}],
            'tests_run':[{'command':'fixture check','outcome':'not_run','details':'offline fixture'}]})+'\n```')
        result=dict(legacy,findings=findings,verification={'tests_run':checks,'limitations':[]})
        self.validator.validate(result)
        for field in ('findings','tests_run'):
            invalid_fields=[('source','independent'),('independently_verified',True)]
            invalid_fields+=([('severity','invented'),('summary','x'*2001),('line',0)] if field=='findings'
                             else [('outcome','invented'),('command','x'*2001)])
            for key,value in invalid_fields:
                invalid=copy.deepcopy(result);items=invalid['findings'] if field=='findings' else invalid['verification']['tests_run']
                items[0][key]=value
                with self.subTest(field=field,key=key),self.assertRaises(ValidationError):self.validator.validate(invalid)
            invalid=copy.deepcopy(result);items=invalid['findings'] if field=='findings' else invalid['verification']['tests_run']
            del items[0]['source']
            with self.subTest(field=field,missing_source=True),self.assertRaises(ValidationError):self.validator.validate(invalid)
            items[0]['source']='worker-reported'
            items.extend([copy.deepcopy(items[0]) for _ in range(19)])
            self.validator.validate(invalid)
            items.append(copy.deepcopy(items[0]))
            with self.subTest(field=field,oversize=True),self.assertRaises(ValidationError):self.validator.validate(invalid)

    async def test_emitted_analysis_and_editing_results_match_the_schema(self):
        for mode in ('analysis','noop','edited','unavailable'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as directory:
                root=Path(directory).resolve();journal=Journal(root/'state');self.addCleanup(journal.close)
                group,worker,task,native=[new_id() for _ in range(4)]
                cwd=root;baseline=None
                if mode!='analysis':
                    repo=root/'repo';repo.mkdir();git(repo,'init','-q')
                    (repo/'base.py').write_text('VALUE = 1\n');git(repo,'add','base.py');git(repo,'commit','-qm','Create result schema fixture')
                    (repo/'brief.txt').write_text('selected input\n')
                    with patch('nativepeers.worktrees.ROOT',root/'project'):
                        work,_,baseline=prepare(journal,repo,worker,{'mode':'isolated-worktree','untracked_allowlist':['brief.txt']})
                    cwd=Path(work)
                    if mode=='edited':(cwd/'answer.py').write_text('ANSWER = 42\n')
                    if mode=='unavailable':baseline.pop('input_tree')
                lease={'provider_home':str(root/'home'),'spec':{'provider':'claude','model':'sonnet','wall_time_seconds':5},
                       'binding':str(root/'binding'),'parent_key':'parent','baseline':baseline}
                journal.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(group,'parent','{}','RUNNING','{}'))
                journal.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (worker,group,'peer','claude',native,'fixture','sonnet',None,None,str(cwd),worker if baseline else None,'RUNNING',1,canonical(lease).decode()))
                journal.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(task,worker,None,None,'QUEUED',None,None,1,'{}'))
                instance=Worker(journal,worker);instance.emit=Mock()
                instance.claude_turn=AsyncMock(return_value={'status':'completed','text':'```nativepeers-result\n'+json.dumps({
                    'findings':[{'severity':'info','summary':'emitted fixture'}],
                    'tests_run':[{'command':'fixture check','outcome':'passed'}]})+'\n```','native_turn_ids':['fixture-turn']})
                await instance.task(task,'offline fixture')
                row=journal.one('SELECT result_artifact_id FROM tasks WHERE task_id=?',(task,))
                page=Artifacts(journal).read(row['result_artifact_id'],'parent')
                result=json.loads(base64.b64decode(page['data_base64']));self.validator.validate(result)
                if mode=='analysis':self.assertNotIn('worker_delta_empty',result['changes'])
                else:
                    changes=result['changes'];self.assertIn('brief.txt',changes['baseline']['untracked_inputs'])
                    self.assertEqual(changes['worker_delta_empty'],mode=='noop')
                    if mode=='edited':self.assertTrue(changes['worker_delta_artifact'])
                    if mode=='unavailable':self.assertTrue(changes['worker_delta_unavailable_reason'])
                    for key,value in [('worker_delta_empty','true'),('worker_delta_artifact',5),('worker_delta_base',False),
                                      ('worker_delta_unavailable_reason',[]),('baseline',{'untracked_inputs':{'brief.txt':{'sha256':'bad','mode':'644'}}})]:
                        invalid=copy.deepcopy(result);invalid['changes'][key]=value
                        with self.subTest(mode=mode,key=key),self.assertRaises(ValidationError):self.validator.validate(invalid)
