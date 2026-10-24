"""Wait and spawn limit failures identify the request without relaxing bounds."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from nativepeers.caller import Caller
from nativepeers.cli import dispatch, parser
from nativepeers.compatibility import fingerprint
from nativepeers.delegation import Delegation
from nativepeers.errors import PeerError
from nativepeers.files import atomic_json
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.registry import Registry


class DelegationDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()
        self.journal=Journal(self.root/'state');self.addCleanup(self.journal.close)
        registry=Registry(self.journal)
        parent=registry.register('claude',self.root/'.claude',new_id(),'limit parent',
                                 identity(os.getpid()).json(),str(self.root),{},owner_kind='test')
        self.caller=Caller('peer',parent['handle'],parent['incarnation_id'])
        self.delegation=Delegation(self.journal,registry)

    def request(self,**limits):
        return {'workers':[{'provider':'claude','title':'limit worker','task':'fixture'}],'limits':limits}

    async def test_wait_rejects_invalid_timeouts_before_result_lookup_or_dispatch(self):
        with patch.object(self.delegation,'terminal_wait') as terminal,patch('nativepeers.delegation.group_call',new_callable=AsyncMock) as dispatch:
            for requested in (30001,60000,0,-1,True,1.5,'30000'):
                with self.subTest(requested=requested),self.assertRaises(PeerError) as error:
                    await self.delegation.wait(self.caller,'unused',requested)
                self.assertEqual(error.exception.code,'PROTOCOL_ERROR')
                self.assertEqual(error.exception.details,{'field':'timeout_ms','requested':requested,'minimum':1,'ceiling':30000})
                self.assertIn(repr(requested),error.exception.message)
                self.assertIn('timeout_ms',error.exception.message);self.assertIn('ceiling 30000',error.exception.message)
            terminal.assert_not_called();dispatch.assert_not_awaited()

    async def test_wait_accepts_both_boundaries_and_preserves_timeout_fallbacks(self):
        with patch.object(self.delegation,'terminal_wait',return_value=None),patch.object(self.delegation,'group',return_value={'group_id':'group','parent_key':self.caller.key}),patch('nativepeers.delegation.group_call',new_callable=AsyncMock,side_effect=lambda *a,**k: {'ready':False}) as dispatch:
            for requested,fallback in ((1,'retain'),(30000,'push')):
                with self.subTest(requested=requested):
                    value=await self.delegation.wait(self.caller,'group',requested,fallback)
                    self.assertFalse(value['ready']);self.assertIn('completion_delivery',value)
                    dispatch.assert_awaited_with(self.journal,'group','wait',{'caller_key':self.caller.key,'handle':'group','timeout_ms':requested,'on_timeout':fallback},requested/1000+2)

    async def test_cli_wait_reports_the_rejected_timeout_without_dispatch(self):
        group=new_id()
        self.journal.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(group,self.caller.key,'{}','RESERVED','{}'))
        args=parser().parse_args(['wait',group,'--timeout-ms','60000','--on-timeout','retain','--json'])
        with patch('nativepeers.cli.bind_caller',return_value=self.caller),patch('nativepeers.delegation.group_call',new_callable=AsyncMock) as control,self.assertRaises(PeerError) as error:
            await dispatch(args,self.journal)
        self.assertEqual(error.exception.code,'PROTOCOL_ERROR')
        self.assertEqual(error.exception.details,{'field':'timeout_ms','requested':60000,'minimum':1,'ceiling':30000})
        control.assert_not_awaited()

    async def test_budget_errors_name_the_offending_field_request_and_current_ceiling(self):
        for policy,field,requested,ceiling in (
            ({},'wall_time_seconds',2400,1800),
            ({},'max_task_turns_per_worker',9,8),
            ({'task_wall_seconds':42},'wall_time_seconds',43,42),
            ({'max_task_turns':3},'max_task_turns_per_worker',4,3),
        ):
            for check in (False,True):
                with self.subTest(field=field,policy=policy,check=check):
                    atomic_json(self.journal.root/'policy.json',policy)
                    with patch('nativepeers.delegation.provider_home') as home,self.assertRaises(PeerError) as error:
                        await self.delegation.spawn(self.caller,self.request(**{field:requested}),check=check)
                    self.assertEqual(error.exception.code,'BUDGET_EXHAUSTED')
                    self.assertEqual(error.exception.details,{'field':'limits.'+field,'requested':requested,'ceiling':ceiling})
                    self.assertEqual(error.exception.message,f'limits.{field} requested {requested} exceeds ceiling {ceiling}')
                    self.assertFalse(error.exception.may_have_received);home.assert_not_called()
                    for table in ('groups','workers','tasks','spawn_requests'):
                        self.assertEqual(self.journal.one('SELECT COUNT(*) n FROM '+table)['n'],0)

    async def test_budget_minima_types_depth_and_concurrency_remain_enforced(self):
        cases=[({field:requested},'PROTOCOL_ERROR') for field in ('wall_time_seconds','max_task_turns_per_worker') for requested in (0,-1,True,1.5)]
        cases.extend((({'max_depth':2},'CAPACITY_UNAVAILABLE'),({'max_concurrent_workers':5},'CAPACITY_UNAVAILABLE')))
        for limits,code in cases:
            with self.subTest(limits=limits),patch('nativepeers.delegation.provider_home') as home,self.assertRaises(PeerError) as error:
                await self.delegation.spawn(self.caller,self.request(**limits))
            self.assertEqual(error.exception.code,code);home.assert_not_called()

    async def test_budget_boundaries_keep_requested_limits_and_delegation_depth(self):
        binary=self.root/'.local/bin/claude';binary.parent.mkdir(parents=True);binary.write_text('metadata fixture only')
        atomic_json(self.journal.root/'compat/runtime.json',{'providers':{'claude':fingerprint(binary)}})
        with patch('nativepeers.delegation.Path.home',return_value=self.root),patch.dict(os.environ,{'CLAUDE_CONFIG_DIR':str(self.root/'.claude')}),patch.object(self.delegation,'ensure_supervisor',new_callable=AsyncMock) as start:
            for wall,turns in ((1,1),(1800,8)):
                with self.subTest(wall=wall,turns=turns):
                    request=self.request(wall_time_seconds=wall,max_task_turns_per_worker=turns)
                    plan=await self.delegation.spawn(self.caller,request,check=True)
                    self.assertTrue(plan['ready'],plan['failures'])
                    worker=plan['workers'][0]
                    self.assertEqual((worker['wall_time_seconds'],worker['max_task_turns']),(wall,turns))
            actual=await self.delegation.spawn(self.caller,self.request())
            lease=json.loads(self.journal.one('SELECT lease_json FROM workers WHERE worker_id=?',(actual['workers'][0]['worker_id'],))['lease_json'])
            self.assertEqual(lease['grants']['max_depth'],1)
            self.assertEqual((lease['spec']['wall_time_seconds'],lease['spec']['max_task_turns']),(1800,8))
            start.assert_awaited_once()

    def test_wait_help_discloses_the_timeout_ceiling_and_default(self):
        output=io.StringIO()
        with patch('sys.stdout',output),self.assertRaises(SystemExit) as exit:
            parser().parse_args(['wait','--help'])
        self.assertEqual(exit.exception.code,0)
        help_text=' '.join(output.getvalue().split())
        self.assertIn('1 <= timeout-ms <= 30000 (default: 30000)',help_text)
        self.assertIn('larger values are rejected',help_text)
        self.assertEqual(parser().parse_args(['wait','group']).timeout_ms,30000)
