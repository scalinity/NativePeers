import asyncio,base64,json,os,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from nativepeers.caller import Caller
from nativepeers.core import Core
from nativepeers.errors import PeerError
from nativepeers.journal import Journal
from nativepeers.mcp_server import Facade
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.registry import Registry


class InboxPageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.j=Journal(Path(temporary.name));self.addCleanup(self.j.close);self.core=Core(self.j)
        peer=self.core.registry.register('codex',self.j.root,new_id(),'inbox',identity(os.getpid()).json(),str(self.j.root),{'kind':'fake'},owner_kind='test')
        self.caller=Caller('peer',peer['handle'],peer['incarnation_id'])

    def message(self,index):
        message=str(index).zfill(4);payload={'message_id':message,'body':{'text':str(index)}}
        self.j.db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?)',(message,'exchange','sender',self.caller.key,None,None,json.dumps(payload),'digest',100,1))
        return message

    async def mcp(self,**args):
        facade=Facade(self.j,None)
        with patch.object(facade,'binding_state',return_value={'state':'BOUND','provider':'codex','grants':{}}),patch('nativepeers.mcp_server.bind_caller',return_value=self.caller):
            response=await facade.call(None,SimpleNamespace(name='peer_inbox',arguments=args))
        self.assertFalse(response.is_error);return json.loads(response.content[0].text)['data']

    async def test_mcp_unread_selection_precedes_limit(self):
        old=self.message(0)
        for i in range(1,111):self.core.router.ack(self.caller,self.message(i))
        rows=await self.mcp(limit=20,unread=True)
        self.assertEqual([r['message_id'] for r in rows],[old])

    async def test_equal_timestamp_pages_exclude_new_insertions_and_preserve_bodies(self):
        expected=[self.message(i) for i in range(7)][::-1]
        self.j.db.execute("UPDATE peer_instances SET owner_kind='interactive-standalone',descriptor_json=json_set(descriptor_json,'$.owner_kind','interactive-standalone')")
        page=await self.mcp(limit=2,paged=True);seen=[]
        self.message(99)
        while True:
            seen.extend(m['message_id'] for m in page['messages'])
            self.assertTrue(all(m['body_available'] is False and 'body' not in m for m in page['messages']))
            if page['next_cursor'] is None:break
            page=await self.mcp(limit=2,cursor=page['next_cursor'])
        self.assertEqual(seen,expected)
        self.assertEqual(self.j.all('SELECT * FROM receipts'),[])

    async def test_cursor_cannot_switch_query_or_caller_and_cli_matches(self):
        from nativepeers.cli import parser,dispatch
        for i in range(3):self.message(i)
        with patch('nativepeers.cli.bind_caller',return_value=self.caller):
            page=await dispatch(parser().parse_args(['inbox','--page','--limit','1']),self.j)
        self.assertEqual(page,await self.mcp(limit=1,paged=True))
        with self.assertRaises(PeerError):self.core.router.inbox(self.caller,1,True,cursor=page['next_cursor'])
        with self.assertRaises(PeerError):self.core.router.inbox(Caller('peer','someone',new_id()),1,cursor=page['next_cursor'])

    async def test_oversized_or_nonfinite_cursors_are_structured_through_cli_and_mcp(self):
        import io
        from nativepeers.cli import main
        from nativepeers.pagination import encode
        fixtures=[([1,'message'],10**100),([10**100,'message'],0),([float('nan'),'message'],0),([float('inf'),'message'],0)]
        for position,ceiling in fixtures:
            with self.subTest(position=position,ceiling=ceiling):
                cursor=base64.urlsafe_b64encode(json.dumps({'scope':['inbox',self.caller.key,False],'position':position,'ceiling':ceiling}).encode()).decode()
                with self.assertRaises(PeerError) as error:self.core.router.inbox(self.caller,cursor=cursor)
                self.assertEqual(error.exception.code,'PROTOCOL_ERROR')
                output=io.StringIO()
                def invoke():
                    try:main()
                    except SystemExit as exit:return exit.code
                with patch('sys.argv',['npeer','inbox','--cursor',cursor,'--json']),patch.dict(os.environ,{'NPEER_HOME':str(self.j.root)}),patch('nativepeers.cli.bind_caller',return_value=self.caller),patch('sys.stdout',output):
                    exit_code=await asyncio.to_thread(invoke)
                self.assertNotEqual(exit_code,0);self.assertEqual(json.loads(output.getvalue())['error']['code'],'PROTOCOL_ERROR')
                facade=Facade(self.j,None)
                with patch.object(facade,'binding_state',return_value={'state':'BOUND','provider':'codex','grants':{}}),patch('nativepeers.mcp_server.bind_caller',return_value=self.caller):
                    response=await facade.call(None,SimpleNamespace(name='peer_inbox',arguments={'cursor':cursor}))
                self.assertTrue(response.is_error);self.assertEqual(json.loads(response.content[0].text)['error']['code'],'PROTOCOL_ERROR')
