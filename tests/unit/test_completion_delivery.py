"""Parent push availability is independent of useful worker admission/results."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
import socket
import sqlite3
from unittest.mock import AsyncMock, patch

from nativepeers.caller import Caller
from nativepeers.cli import dispatch, parser
from nativepeers.compatibility import fingerprint
from nativepeers.completion_delivery import readiness
from nativepeers.core import Core
from nativepeers.delegation import Delegation
from nativepeers.errors import PeerError
from nativepeers.files import atomic_json, canonical
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.registry import Registry
from nativepeers.supervisor import Group


class CompletionDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve(); self.home = self.root/'home'
        self.claude = self.home/'.local/bin/claude'
        self.codex = self.home/'.local/share/codex-mcp-manager/current/bin/codex'
        for binary in (self.claude, self.codex):
            binary.parent.mkdir(parents=True, exist_ok=True); binary.write_text('fixture runtime')
        (self.home/'.claude').mkdir(); (self.home/'.claude/settings.json').write_text('{"model":"sonnet"}')
        self.j = Journal(self.root/'state'); self.addCleanup(self.j.close)
        atomic_json(self.j.root/'compat/runtime.json', {'providers': {
            'claude': fingerprint(self.claude), 'codex': fingerprint(self.codex)}})
        self.enterContext(patch('nativepeers.registry.matches',return_value=True))
        peer = Registry(self.j).register('codex', self.home/'.codex', new_id(), 'parent',
            dict(identity(os.getpid()).json(),executable=str(self.codex)), str(self.root), {'kind':'codex-native-queue'},
            owner_kind='test', policy={'profiles':['analysis']})
        self.caller = Caller('peer', peer['handle'], peer['incarnation_id'])
        self.enterContext(patch('pathlib.Path.home', return_value=self.home))
        self.enterContext(patch.dict(os.environ, {'CLAUDE_CONFIG_DIR':str(self.home/'.claude')}))

    async def test_preflight_warns_without_refusing_useful_workers_and_cli_mcp_agree(self):
        self.codex.write_text('changed parent runtime')
        spec = {'client_request_id':'completion-check', 'workers':[
            {'provider':'claude','title':'useful','task':'fixture'}]}
        path = self.root/'spec.json'; path.write_text(json.dumps(spec))
        core = Core(self.j)
        with patch('nativepeers.cli.bind_caller', return_value=self.caller):
            cli = await dispatch(parser().parse_args(['spawn','--check','--spec',str(path)]), self.j)
        mcp = await core.tool(self.caller, 'worker_spawn', {'spec':spec,'check':True})
        for plan in (cli, mcp):
            self.assertTrue(plan['ready']); self.assertEqual(plan['ready_scope'],'worker_execution')
            self.assertFalse(plan['push_ready'])
            self.assertEqual(plan['completion_delivery']['state'],'CAPABILITY_MISMATCH')
            self.assertIn('CAPABILITY_MISMATCH',plan['warnings'][0])
        with patch.object(Delegation,'ensure_supervisor',new_callable=AsyncMock):
            spawned = await core.delegation.spawn(self.caller,spec)
        self.assertFalse(spawned['push_ready']); self.assertEqual(len(spawned['workers']),1)
        group = spawned['group_id']
        expired = {'ready':False,'state':'WAIT_EXPIRED','notification_disposition':'push'}
        with patch('nativepeers.delegation.group_call',new_callable=AsyncMock,return_value=expired):
            waited = await core.tool(self.caller,'task_wait',{'handle':group,'timeout_ms':1,'on_timeout':'push'})
        self.assertIn('CAPABILITY_MISMATCH',waited['warnings'][0])
        self.assertFalse(waited['completion_delivery']['ready'])
        with patch('nativepeers.cli.bind_caller',return_value=self.caller):
            who = await dispatch(parser().parse_args(['whoami']),self.j)
        self.assertEqual(who['inbox_readiness']['state'],'CAPABILITY_MISMATCH')
        self.assertEqual(who['native_session_id'],Registry(self.j).get(self.caller.key)['peer_key']['native_session_id'])

    async def test_retained_refusals_remain_attributed_to_followup_and_resume_generations(self):
        spec = {'completion_mode':'push','workers':[{'provider':'claude','title':'retained','task':'fixture'}]}
        with patch.object(Delegation,'ensure_supervisor',new_callable=AsyncMock):
            spawned = await Core(self.j).delegation.spawn(self.caller,spec)
        group_id = spawned['group_id']; task = spawned['tasks'][0]['task_id']
        self.j.db.execute("UPDATE tasks SET state='FAILED',terminal_reason='fixture' WHERE task_id=?",(task,))
        group = Group(self.j,group_id)
        # Exercise the real router/journal, replacing only native execution.
        for generation, code in ((1,'CAPABILITY_MISMATCH'),(2,'PROVIDER_POLICY_REFUSED'),(3,'HELD')):
            if generation>1:
                policy=json.loads(self.j.one('SELECT policy_json FROM groups WHERE group_id=?',(group_id,))['policy_json'])
                policy['phases'][str(generation)]=[task]
                # The third phase represents a released worker's new supervisor generation.
                policy['controller_generation']=2 if generation==3 else 1
                self.j.db.execute('UPDATE groups SET policy_json=? WHERE group_id=?',(canonical(policy).decode(),group_id))
                self.j.db.execute('INSERT INTO completion_routes VALUES(?,?,?,?,?,?,?,?,?)',
                    (group_id,generation,self.caller.key,'push','UNCLAIMED',None,None,new_id(),policy['controller_generation']))
                group=Group(self.j,group_id)
            core=Core(self.j)
            async def deliver(peer,message,*,admission_deadline=None):
                self.assertIsNotNone(admission_deadline)
                if code=='CAPABILITY_MISMATCH':raise PeerError(code,'exact fixture mismatch')
                return {'stage':'REJECTED' if code=='PROVIDER_POLICY_REFUSED' else 'HELD','evidence_kind':'fixture-policy'}
            core.router.adapters['codex-native-queue']=deliver
            with patch('nativepeers.core.Core',return_value=core):await group.notify(generation)
        status=Core(self.j).delegation.status(self.caller,group_id)
        routes=status['notifications']
        self.assertEqual([r['state'] for r in routes],['RETAINED','RETAINED','HELD'])
        self.assertEqual([r['delivery']['state'] for r in routes],['CAPABILITY_MISMATCH','REJECTED','HELD'])
        self.assertEqual(routes[0]['delivery']['error']['message'],'exact fixture mismatch')
        self.assertEqual(routes[1]['delivery']['error']['code'],'PROVIDER_POLICY_REFUSED')
        result=Core(self.j).delegation.result(self.caller,group_id)
        self.assertEqual(result['notifications'],routes); self.assertTrue(result['results'])
        attempts=self.j.all('SELECT * FROM attempts')
        # A result read and repeated notify cannot resubmit refused/held work.
        await group.notify(3)
        self.assertEqual(self.j.all('SELECT * FROM attempts'),attempts)
        # A later native receipt must be read from the exact controller message,
        # not the completion event's earlier HELD snapshot.
        router=Core(self.j).router;message=routes[-1]['delivery']['message_id']
        outcome=router.outcome(message)
        router.record(message,outcome['attempt']['attempt_id'],{'stage':'INGESTED','evidence_kind':'fixture-late-native'},Registry(self.j).get(self.caller.key))
        updated=Core(self.j).delegation.result(self.caller,group_id)
        self.assertEqual(updated['notifications'][-1]['delivery']['state'],'INGESTED')

    async def test_idempotent_admission_race_inspects_parent_only_after_transaction(self):
        spec={'client_request_id':'race','workers':[{'provider':'claude','title':'race','task':'fixture'}]}
        core=Core(self.j)
        with patch.object(Delegation,'ensure_supervisor',new_callable=AsyncMock):
            original=await core.delegation.spawn(self.caller,spec)
        one=self.j.one;skipped=False
        def query(sql,params=()):
            nonlocal skipped
            if sql.startswith('SELECT * FROM spawn_requests') and not skipped:
                skipped=True;return None
            return one(sql,params)
        def route(journal,parent):
            self.assertFalse(journal.db.in_transaction)
            return {'ready':True,'state':'READY','reason':'fixture','parent_key':parent}
        with patch.object(self.j,'one',side_effect=query),patch('nativepeers.completion_delivery.readiness',side_effect=route) as route_check,patch.object(Delegation,'ensure_supervisor',new_callable=AsyncMock) as launch:
            raced=await core.delegation.spawn(self.caller,spec)
        route_check.assert_called_once()
        self.assertEqual(raced['group_id'],original['group_id']);launch.assert_not_awaited()
        self.assertEqual(self.j.one('SELECT COUNT(*) n FROM groups')['n'],1)

    def test_offline_parent_is_explicit_without_quota_or_mcp_inference(self):
        self.j.db.execute("UPDATE peer_instances SET status='offline'")
        route=readiness(self.j,self.caller.key)
        self.assertEqual(route['state'],'TARGET_OFFLINE'); self.assertFalse(route['ready'])
        self.assertIn('retained',route['result_collection'])

    async def test_terminal_wait_diagnostics_run_once_after_claim_commit_and_match_cli_mcp(self):
        with patch.object(Delegation,'ensure_supervisor',new_callable=AsyncMock):
            spawned=await Core(self.j).delegation.spawn(self.caller,{'workers':[{'provider':'claude','title':'terminal','task':'fixture'}]})
        self.j.db.execute("UPDATE tasks SET state='FAILED',terminal_reason='fixture'")
        def route(journal,parent):
            self.assertFalse(journal.db.in_transaction)
            self.assertEqual(parent,self.caller.key)
            return {'ready':False,'state':'UNKNOWN','reason':'fixture diagnostic','parent_key':parent}
        with patch('nativepeers.completion_delivery.readiness',side_effect=route) as check:
            mcp=await Core(self.j).tool(self.caller,'task_wait',{'handle':spawned['group_id']})
            self.assertEqual(check.call_count,1)
        with patch('nativepeers.completion_delivery.readiness',side_effect=route),patch('nativepeers.cli.bind_caller',return_value=self.caller):
            cli=await dispatch(parser().parse_args(['wait',spawned['group_id']]),self.j)
        self.assertTrue(cli['ready']);self.assertTrue(mcp['ready'])
        self.assertEqual(cli['completion_delivery'],mcp['completion_delivery'])

    def test_passive_diagnostic_errors_do_not_escape(self):
        from nativepeers.completion_delivery import describe
        with patch('nativepeers.completion_delivery.Registry.get',side_effect=sqlite3.OperationalError('busy')):
            self.assertEqual(readiness(self.j,self.caller.key)['state'],'UNKNOWN')
        with patch.object(self.j,'one',side_effect=sqlite3.OperationalError('busy')):
            value=describe(self.j,{'delivery_id':new_id(),'parent_key':self.caller.key,'group_id':new_id()})
        self.assertEqual(value['delivery']['state'],'UNKNOWN')

    def test_socket_owned_and_next_invocation_routes(self):
        registry=Registry(self.j);owner=identity(os.getpid()).json()
        endpoint=self.root/'s';listener=socket.socket(socket.AF_UNIX)
        try:
            listener.bind(str(endpoint));endpoint.chmod(0o600)
            peer=registry.register('claude',self.home/'.claude',new_id(),'socket',owner,self.root,
                {'kind':'claude-inbox','endpoint':str(endpoint)},owner_kind='test')
            self.assertTrue(readiness(self.j,peer['handle'])['ready'])
            endpoint.unlink();self.assertFalse(readiness(self.j,peer['handle'])['ready'])
        finally:listener.close()
        peer=registry.register('codex',self.home/'.codex',new_id(),'owned',owner,self.root,
            {'kind':'owned-worker','worker_id':'fixture-worker'},owner_kind='test')
        with patch.object(self.j,'one',wraps=self.j.one) as query:
            original=self.j.one
            query.side_effect=lambda sql,params=(): {'state':'IDLE_GRACE','generation':2} if sql.startswith('SELECT state,generation') else query._mock_wraps(sql,params)
            self.assertEqual(readiness(self.j,peer['handle'])['generation'],2)
        peer=registry.register('antigravity',self.home/'.gemini',new_id(),'hook',owner,self.root,
            {'kind':'antigravity-native-hook'},owner_kind='test')
        with patch('nativepeers.compatibility.verify',return_value={'sha256':'fixture'}):
            route=readiness(self.j,peer['handle'])
        self.assertEqual(route['state'],'NEXT_INVOCATION');self.assertFalse(route['ready'])

    async def test_activation_deferred_completion_retries_at_bounded_interval_only(self):
        with patch.object(Delegation,'ensure_supervisor',new_callable=AsyncMock):
            spawned=await Core(self.j).delegation.spawn(self.caller,{'completion_mode':'push','workers':[{'provider':'claude','title':'deferred','task':'fixture'}]})
        self.j.db.execute("UPDATE tasks SET state='COMPLETED'")
        group=Group(self.j,spawned['group_id']);core=Core(self.j);calls=[]
        async def deferred(peer,message,*,admission_deadline=None):
            self.assertIsNotNone(admission_deadline)
            calls.append(message['message_id'])
            raise PeerError('CAPABILITY_MISMATCH','candidate awaits qualification',details={'compatibility_state':'UPDATE_UNTESTED'})
        core.router.adapters['codex-native-queue']=deferred
        with patch('nativepeers.core.Core',return_value=core):
            await group.notify();await group.notify()
            self.assertEqual(len(calls),1)
            self.j.db.execute('UPDATE attempts SET ended_at=ended_at-16')
            await group.notify()
        self.assertEqual(calls,[calls[0],calls[0]])
        self.assertEqual(self.j.one('SELECT COUNT(*) n FROM attempts')['n'],1)

    async def test_approved_recipient_readiness_survives_unqualified_selection_and_doctor_is_passive(self):
        from nativepeers.readiness import operation_readiness
        old=self.root/'immutable-approved';old.write_bytes(self.codex.read_bytes())
        atomic_json(self.j.root/'compat/runtime.json',{'providers':{'claude':fingerprint(self.claude),'codex':fingerprint(old)}})
        peer=Registry(self.j).register('codex',self.home/'.codex',new_id(),'approved parent',
              dict(identity(os.getpid()).json(),executable=str(old)),self.root,{'kind':'codex-native-queue'},policy={'profiles':['analysis']})
        caller=Caller('peer',peer['handle'],peer['incarnation_id'])
        self.codex.write_text('new selected unqualified runtime')
        route=readiness(self.j,caller.key)
        self.assertTrue(route['ready']);self.assertEqual(route['runtime'],fingerprint(old))
        with patch('nativepeers.cli.bind_caller',return_value=caller):
            who=await dispatch(parser().parse_args(['whoami']),self.j)
        self.assertTrue(who['inbox_readiness']['ready'])
        plan=await Core(self.j).tool(caller,'worker_spawn',{'spec':{'workers':[{'provider':'claude','title':'useful','task':'fixture'}]},'check':True})
        self.assertTrue(plan['push_ready'])
        before=self.j.db.total_changes;receipt=(self.j.root/'compat/runtime.json').read_bytes()
        row=next(r for r in operation_readiness(self.j) if r['provider']=='codex')
        self.assertEqual(row['operations']['inbox']['state'],'READY')
        self.assertEqual(row['operations']['worker_turns']['state'],'CAPABILITY_MISMATCH')
        self.assertEqual(row['operations']['inbox']['selected_worker_runtime']['state'],'CAPABILITY_MISMATCH')
        self.assertEqual(self.j.db.total_changes,before)
        self.assertEqual((self.j.root/'compat/runtime.json').read_bytes(),receipt)
