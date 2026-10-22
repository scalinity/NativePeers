import asyncio,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

from nativepeers.caller import Caller
from nativepeers.cli import dispatch,parser
from nativepeers.delegation import Delegation
from nativepeers.errors import PeerError
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.registry import Registry


class GroupDiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.j=Journal(Path(temporary.name));self.addCleanup(self.j.close);self.registry=Registry(self.j)
        parent=self.registry.register('codex',self.j.root,new_id(),'parent',identity(os.getpid()).json(),str(self.j.root),{'kind':'fake'},owner_kind='test')
        self.caller=Caller('peer',parent['handle'],parent['incarnation_id']);self.service=Delegation(self.j,self.registry)

    def group(self,parent=None):
        group,worker,task=new_id(),new_id(),new_id()
        self.j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(group,parent or self.caller.key,'{}','COMPLETED','{}'))
        self.j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(worker,group,None,'codex',new_id(),'retained title','model','model',None,str(self.j.root),None,'RELEASED_RESUMABLE',1,'{}'))
        self.j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(task,worker,None,None,'COMPLETED',None,None,1,'{}'))
        self.j.db.execute('INSERT INTO completion_routes VALUES(?,?,?,?,?,?,?,?,?)',(group,1,parent or self.caller.key,'push','UNCLAIMED',None,None,new_id(),1))
        return group

    def test_stable_parent_filtered_pages_retain_completed_groups_and_leave_claims_alone(self):
        wanted=[self.group() for _ in range(5)];self.group('other-parent')
        before=self.j.all('SELECT * FROM completion_routes')
        with patch('nativepeers.cli.bind_caller',return_value=self.caller):
            page=asyncio.run(dispatch(parser().parse_args(['groups','--mine','--limit','2']),self.j))
        self.assertEqual([g['group_id'] for g in page['groups']],wanted[::-1][:2])
        self.assertEqual(page['groups'][0]['workers'][0]['role_title'],'retained title')
        self.assertFalse(page['groups'][0]['results_available'])
        later=self.group();collected=[g['group_id'] for g in page['groups']]
        while page['next_cursor']:
            page=self.service.groups(self.caller,2,page['next_cursor']);collected.extend(g['group_id'] for g in page['groups'])
        self.assertEqual(collected,wanted[::-1]);self.assertNotIn(later,collected)
        self.assertEqual(self.j.all('SELECT * FROM completion_routes')[:-1],before)
        with self.assertRaises(PeerError):self.service.groups(Caller('external-local','local'))
        with self.assertRaises(PeerError):self.service.groups(self.caller,101)

    def test_cursor_is_scoped_and_results_are_discoverable_without_a_supervisor(self):
        group=self.group();row=self.j.one('SELECT task_id FROM tasks')
        self.j.db.execute("UPDATE tasks SET result_artifact_id='retained-handle' WHERE task_id=?",(row['task_id'],))
        self.group();page=self.service.groups(self.caller,1)
        with self.assertRaises(PeerError):self.service.groups(self.caller,1,'not-json')
        result=self.service.groups(self.caller,1,page['next_cursor'])['groups'][0]
        self.assertEqual(result['group_id'],group);self.assertTrue(result['results_available']);self.assertEqual(result['result_count'],1)
        from nativepeers.pagination import encode
        with self.assertRaises(PeerError):self.service.groups(self.caller,1,encode(['groups','someone-else'],1,2))
