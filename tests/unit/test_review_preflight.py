import json
from unittest.mock import patch
import unittest
import test_spawn_check as fixture
from nativepeers.core import Core
from nativepeers.selected_context import prepare
from nativepeers.worktrees import git


class ReviewPreflightTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.SpawnCheckTests.asyncSetUp
    request=fixture.SpawnCheckTests.request
    rows=fixture.SpawnCheckTests.rows
    async def test_compact_diff_preflight_requires_packet_and_preserves_counters(self):
        request=self.request();request['limits']=dict(max_task_turns_per_worker=3,max_native_turns_per_task=24,wall_time_seconds=120)
        request['workers'][0]['requirements']=dict(task_type='review',required_tools=['git_diff'])
        before=self.rows()
        value=await self.delegation.spawn(self.caller,request,check=True,compact=True)
        self.assertFalse(value['ready']);self.assertEqual(value['failures'][0]['error']['code'],'REVIEW_CONTEXT_REQUIRED')
        self.assertIn('context-prepare',value['failures'][0]['error']['message']);self.assertEqual(before,self.rows())
        git(self.source,'init','-q');git(self.source,'add','main.py');git(self.source,'commit','-qm','Create bounded review fixture')
        (self.source/'main.py').write_text('VALUE=2\n')
        info=prepare(self.j,self.caller,['main.py'])
        request['workers'][0]['context_refs']=info['context_refs']
        before=self.rows()
        value=await self.delegation.spawn(self.caller,request,check=True,compact=True)
        mcp=await Core(self.j).tool(self.caller,'worker_spawn',dict(spec=request,check=True,compact=True))
        # Owner observation and exact selections have parity; readiness clocks are not a dispatch receipt.
        self.assertEqual(value['workers'],mcp['workers']);self.assertTrue(value['ready'])
        self.assertEqual(value['limits']['max_task_turns_per_worker']['effective'],3)
        self.assertEqual(value['limits']['max_native_turns_per_task']['effective'],24)
        self.assertIn('artifact_read',value['workers'][0]['tools']['planned'])
        self.assertEqual(value['workers'][0]['review_context'][0]['artifact_id'],info['artifact_id'])
        self.assertFalse(value['capacity']['reservation_made']);self.assertFalse(value['model_input_submitted'])
        self.assertEqual(before,self.rows());self.start.assert_not_awaited()
