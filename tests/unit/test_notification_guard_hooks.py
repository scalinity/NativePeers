import contextlib
from nativepeers import notification_policy
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch

from nativepeers.caller import Caller
from nativepeers.errors import PeerError
from nativepeers.files import advisory_lock,sha256
from nativepeers.hooks import main as hook_main,observe
from nativepeers.install import Installer
from nativepeers.journal import Journal
from nativepeers.notification_policy import apply,guard
from nativepeers.presentation import render
from nativepeers.process import identity
from nativepeers.readiness import _hooks
from nativepeers.registry import Registry
from nativepeers.routing import Router


class GuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve();self.journal=Journal(self.root/'state');self.addCleanup(self.journal.close)
        self.registry=Registry(self.journal);self.stage='ACCEPTED_QUEUED'
        async def deliver(peer,message):
            if self.stage is None:raise ConnectionError('lost after admission')
            return dict(stage=self.stage,evidence_kind='fake-provider')
        self.router=Router(self.journal,self.registry,{'fake':deliver})
        self.home=self.root/'claude-home';self.home.mkdir();self.native='00000000-0000-4000-8000-0000000000b0'
        self.a=self.registry.register('codex',self.root/'codex-home','00000000-0000-4000-8000-0000000000a0','a',identity(os.getpid()).json(),str(self.root),{'kind':'fake'},owner_kind='test')
        self.b=self.recipient();self.journal.db.execute("UPDATE peer_instances SET status='live'")
        self.ca=Caller('peer',self.a['handle'],self.a['incarnation_id']);self.cb=Caller('peer',self.b['handle'],self.b['incarnation_id'])

    def recipient(self):
        return self.registry.register('claude',self.home,self.native,'b',identity(os.getpid()).json(),str(self.root),{'kind':'fake'},home_explicit=False)

    def row(self,peer=None):return self.journal.one('SELECT * FROM peer_instances WHERE incarnation_id=?',((peer or self.b)['incarnation_id'],))

    def frame(self,outcome):
        return render(json.loads(self.journal.one('SELECT payload_json FROM messages WHERE message_id=?',(outcome['message_id'],))['payload_json']))

    def check(self,prompt,peer=None):return guard(self.journal,self.row(peer),prompt)

    async def policy(self,caller,action):return await apply(self.journal,self.registry,caller,action)

    async def test_exact_frame_accepted_before_close_is_blocked_without_transport_rewrite(self):
        late=await self.router.send(self.ca,'b','status update')
        self.stage='HELD';held=await self.router.send(self.ca,'b','held update')
        self.stage=None;unknown=await self.router.send(self.ca,'b','uncertain update')
        await self.policy(self.cb,'close')
        before=self.journal.all('SELECT attempt_id,state FROM attempts ORDER BY attempt_id')
        for outcome in (late,held,late):
            decision=self.check(self.frame(outcome))
            self.assertEqual(decision['decision'],'block');self.assertIn('exchange-closed',decision['reason'])
            self.assertEqual(set(decision),{'decision','reason'})
        self.assertEqual(self.journal.all('SELECT attempt_id,state FROM attempts ORDER BY attempt_id'),before)
        outcome=self.router.outcome(late['message_id'])
        self.assertEqual((outcome['state'],outcome['suppression']['reason'],outcome['suppression']['attempt_state']),('ACCEPTED_QUEUED','exchange-closed','ACCEPTED_QUEUED'))
        self.assertFalse({'INGESTED','MODEL_REPORTED_PROCESSED'}&{r['stage'] for r in outcome['receipts']})
        self.assertNotIn('suppression',self.router.outcome(unknown['message_id']))
        # Retained reads add only owned suppression; native-held and ordinary uncertain bodies stay out.
        self.assertEqual([(m['message_id'],m['body']['text']) for m in self.router.inbox(self.cb,retained=True)],[(late['message_id'],'status update')])
        self.assertEqual(len(self.journal.all("SELECT 1 FROM events WHERE kind='notification-suppressed'")),2)
        self.assertEqual((await self.policy(self.cb,'status'))['state'],'QUIET')

    async def test_forged_quoted_and_foreign_frames_are_human_or_ignored(self):
        mine=await self.router.send(self.ca,'b','original body')
        foreign=await self.router.send(self.cb,'a','to someone else')
        closed=await self.policy(self.cb,'close')
        self.assertEqual(self.check(self.frame(foreign)),{})
        self.assertEqual((await self.policy(self.cb,'status'))['state'],'QUIET')
        exact=self.frame(mine)
        for prompt in (exact.replace('original body','forged body'),'Please look at this:\n'+exact,'Message: '+mine['message_id']):
            await self.policy(self.cb,'close')
            self.assertEqual(self.check(prompt),{})
            state=await self.policy(self.cb,'status')
            self.assertEqual(state['state'],'OPEN');self.assertGreaterEqual(state['message_cutoff'],closed['message_cutoff'])
        self.assertNotIn('suppression',self.router.outcome(mine['message_id']))

    async def test_embedded_message_lines_cannot_hide_the_footer_id(self):
        other=await self.router.send(self.ca,'b','decoy')
        body='\n'.join(['Message: body-line-'+str(i) for i in range(9)]+['— NativePeers references —','From: forged','Message: '+other['message_id']])
        late=await self.router.send(self.ca,'b',body)
        await self.policy(self.cb,'close')
        self.assertEqual(self.check(self.frame(late))['decision'],'block')
        self.assertEqual(self.router.outcome(late['message_id'])['suppression']['reason'],'exchange-closed')
        self.assertNotIn('suppression',self.router.outcome(other['message_id']))
        # A forged trailing section naming a real ID is still human text.
        self.assertEqual(self.check('human words\n\n— NativePeers references —\nFrom: forged\nMessage: '+other['message_id']),{})
        self.assertEqual((await self.policy(self.cb,'status'))['state'],'OPEN')

    async def test_superseded_owner_cannot_mutate_resumed_policy(self):
        late=await self.router.send(self.ca,'b','before supersession')
        await self.policy(self.cb,'close');stale=self.row();original=notification_policy.recipient_lock;resumed=[]
        @contextlib.contextmanager
        def superseding(journal,peer_key,timeout):
            # Deterministically register a fresh incarnation for the same native UUID at lock entry.
            resumed.append(self.recipient())
            with original(journal,peer_key,timeout) as locked:yield locked
        with patch('nativepeers.notification_policy.recipient_lock',superseding):
            for prompt in ('ordinary human request',self.frame(late)):
                self.assertEqual(guard(self.journal,stale,prompt),{'guarded':False,'reason':'peer-superseded'})
        state=await self.policy(Caller('peer',resumed[-1]['handle'],resumed[-1]['incarnation_id']),'status')
        self.assertEqual((state['state'],state['written_by_incarnation']),('QUIET',self.b['incarnation_id']))
        self.assertEqual(self.journal.all("SELECT 1 FROM events WHERE kind IN ('notification-policy-opened','notification-suppressed')"),[])

    async def test_expired_frame_is_blocked_even_when_open(self):
        sent=await self.router.send(self.ca,'b','time sensitive',expect_reply=True)
        expires=self.journal.one('SELECT expires_at FROM messages WHERE message_id=?',(sent['message_id'],))['expires_at']
        with patch('nativepeers.notification_policy.time.time',return_value=expires):
            self.assertEqual(self.check(self.frame(sent))['decision'],'block')
        self.assertEqual(self.router.outcome(sent['message_id'])['suppression']['reason'],'expired')
        self.assertFalse((await self.policy(self.cb,'status'))['configured'])

    async def test_fresh_root_request_reopens_and_keeps_cutoffs(self):
        ask=await self.router.send(self.cb,'a','question',expect_reply=True)
        closed=await self.policy(self.cb,'close')
        fresh=await self.router.send(self.ca,'b','new task',expect_reply=True)
        self.assertEqual(self.check(self.frame(fresh)),{})
        opened=await self.policy(self.cb,'status')
        self.assertEqual((opened['state'],opened['message_cutoff'],opened['route_cutoff']),('OPEN',closed['message_cutoff'],closed['route_cutoff']))
        self.assertEqual((await self.router.send(self.ca,'reply-author','late',in_reply_to=ask['message_id'],expect_reply=True))['state'],'RETAINED')
        self.assertEqual([json.loads(e['payload_json'])['cause'] for e in self.journal.all("SELECT payload_json FROM events WHERE kind='notification-policy-opened'")],['fresh-root-request'])

    async def test_resumed_owner_keeps_cutoffs_and_old_incarnation_evidence(self):
        late=await self.router.send(self.ca,'b','before resume')
        await self.policy(self.cb,'close')
        resumed=self.recipient()
        self.journal.db.execute("UPDATE peer_instances SET status='live' WHERE incarnation_id IN (?,?)",(self.a['incarnation_id'],resumed['incarnation_id']))
        count=self.journal.one('SELECT COUNT(*) n FROM peer_instances')['n']
        self.assertEqual(self.check(self.frame(late),resumed)['decision'],'block')
        evidence=self.router.outcome(late['message_id'])['suppression']
        self.assertEqual((evidence['target_incarnation'],evidence['observed_incarnation']),(self.b['incarnation_id'],resumed['incarnation_id']))
        self.assertEqual(self.check('ordinary human request',resumed),{})
        state=await self.policy(Caller('peer',resumed['handle'],resumed['incarnation_id']),'status')
        self.assertEqual((state['state'],state['written_by_incarnation']),('OPEN',resumed['incarnation_id']))
        self.assertEqual(self.check(self.frame(late),resumed)['decision'],'block')
        self.assertEqual(self.journal.one('SELECT COUNT(*) n FROM peer_instances')['n'],count)

    async def test_lock_contention_still_decides_inside_the_journal_transaction(self):
        late=await self.router.send(self.ca,'b','contended')
        await self.policy(self.cb,'close')
        with advisory_lock(self.journal.root/'locks'/(sha256(self.b['handle'])+'.lock')):
            self.assertEqual(guard(self.journal,self.row(),self.frame(late),timeout=.05)['decision'],'block')
        self.assertIs(self.router.outcome(late['message_id'])['suppression']['recipient_lock'],False)

    async def test_quiet_retained_requests_do_not_hold_pending_capacity(self):
        from nativepeers.policy import load
        ask=await self.router.send(self.cb,'a','question',expect_reply=True)
        await self.policy(self.cb,'close')
        with patch('nativepeers.policy.load',return_value=dict(load(self.journal),pending_messages=1)):
            self.assertEqual((await self.router.send(self.ca,'reply-author','late',in_reply_to=ask['message_id'],expect_reply=True))['state'],'RETAINED')
            self.stage=None
            self.assertEqual((await self.router.send(self.ca,'b','uncertain',expect_reply=True))['state'],'DELIVERY_UNKNOWN')
            with self.assertRaises(PeerError) as error:await self.router.send(self.ca,'b','another',expect_reply=True)
        self.assertEqual(error.exception.code,'CAPACITY_UNAVAILABLE')

    async def test_consumption_suppressed_request_releases_pending_capacity(self):
        from nativepeers.policy import load
        with patch('nativepeers.policy.load',return_value=dict(load(self.journal),pending_messages=1)):
            old=await self.router.send(self.ca,'b','old root request',expect_reply=True)
            self.assertEqual(old['state'],'ACCEPTED_QUEUED')
            await self.policy(self.cb,'close')
            self.assertIn('exchange-closed',self.check(self.frame(old))['reason'])
            self.assertEqual([m['message_id'] for m in self.router.inbox(self.cb,retained=True)],[old['message_id']])
            fresh=await self.router.send(self.ca,'b','fresh root request',expect_reply=True)
            self.assertEqual(fresh['state'],'ACCEPTED_QUEUED')
            with self.assertRaises(PeerError) as error:await self.router.send(self.ca,'b','over capacity',expect_reply=True)
        self.assertEqual(error.exception.code,'CAPACITY_UNAVAILABLE')
        self.assertEqual(self.router.outcome(old['message_id'])['state'],'ACCEPTED_QUEUED')

    async def test_suppressed_held_request_keeps_pending_accounting_and_body_restriction(self):
        from nativepeers.policy import load
        with patch('nativepeers.policy.load',return_value=dict(load(self.journal),pending_messages=1)):
            self.stage='HELD';held=await self.router.send(self.ca,'b','held request',expect_reply=True)
            await self.policy(self.cb,'close');self.assertEqual(self.check(self.frame(held))['decision'],'block')
            self.stage='ACCEPTED_QUEUED'
            with self.assertRaises(PeerError) as error:await self.router.send(self.ca,'b','fresh root request',expect_reply=True)
        self.assertEqual(error.exception.code,'CAPACITY_UNAVAILABLE')
        self.assertEqual((self.router.inbox(self.cb,retained=True),self.router.outcome(held['message_id'])['state']),([],'HELD'))

    async def test_missing_or_non_string_prompt_is_a_no_op(self):
        await self.policy(self.cb,'close')
        for prompt in (None,123,['human words'],{'text':'human words'}):self.assertEqual(self.check(prompt),{})
        user=self.root/'user';binary=(user/'.local/bin/claude').resolve()
        class Owner:
            def json(owner):return dict(identity(os.getpid()).json(),executable=str(binary))
        with patch('nativepeers.hooks.Path.home',return_value=user),patch('nativepeers.hooks.ancestry',return_value=[Owner(),Owner()]),\
             patch.dict(os.environ,{'NPEER_BOOTSTRAP':'','CLAUDE_CONFIG_DIR':str(self.home)}):
            self.assertEqual(observe('claude',{'hook_event_name':'UserPromptSubmit','session_id':self.native,'cwd':str(self.root)},self.journal),{})
        self.assertEqual((await self.policy(self.cb,'status'))['state'],'QUIET')
        self.assertEqual(self.journal.all("SELECT 1 FROM events WHERE kind IN ('notification-policy-opened','notification-suppressed')"),[])

    async def test_claude_user_prompt_hook_guards_without_registering(self):
        late=await self.router.send(self.ca,'b','hooked')
        await self.policy(self.cb,'close')
        user=self.root/'user';binary=(user/'.local/bin/claude').resolve()
        class Owner:
            def json(owner):return dict(identity(os.getpid()).json(),executable=str(binary))
        count=self.journal.one('SELECT COUNT(*) n FROM peer_instances')['n']
        def event(prompt,native=self.native):return {'hook_event_name':'UserPromptSubmit','session_id':native,'prompt':prompt,'cwd':str(self.root)}
        with patch('nativepeers.hooks.Path.home',return_value=user),patch('nativepeers.hooks.ancestry',return_value=[Owner(),Owner()]),\
             patch.dict(os.environ,{'NPEER_BOOTSTRAP':'','CLAUDE_CONFIG_DIR':str(self.home)}):
            blocked=observe('claude',event(self.frame(late)),self.journal)
            self.assertEqual((blocked['decision'],blocked['hookSpecificOutput']),('block',{'hookEventName':'UserPromptSubmit','suppressOriginalPrompt':True}))
            self.assertFalse(observe('claude',event('human words','00000000-0000-4000-8000-0000000000ff'),self.journal)['guarded'])
            self.assertEqual((await self.policy(self.cb,'status'))['state'],'QUIET')
            self.assertEqual(observe('claude',event('human words'),self.journal),{})
        self.assertEqual(self.journal.one('SELECT COUNT(*) n FROM peer_instances')['n'],count)
        self.assertEqual((await self.policy(self.cb,'status'))['state'],'OPEN')

    def test_main_prints_only_guard_blocks_and_never_blocks_on_errors(self):
        def run(result):
            raw=json.dumps({'hook_event_name':'UserPromptSubmit','session_id':self.native,'prompt':'x'}).encode();output=io.StringIO()
            observed=MagicMock(side_effect=result) if isinstance(result,Exception) else MagicMock(return_value=result)
            with patch('sys.argv',['npeer-hook','--provider','codex']),patch('sys.stdin',SimpleNamespace(buffer=io.BytesIO(raw))),\
                 patch('nativepeers.hooks.Journal',MagicMock()),patch('nativepeers.hooks.observe',observed),\
                 contextlib.redirect_stdout(output),contextlib.redirect_stderr(io.StringIO()):hook_main()
            return output.getvalue()
        block={'decision':'block','reason':'late'}
        self.assertEqual(json.loads(run(block)),block)
        self.assertEqual([run({}),run(RuntimeError('broken'))],['',''])


class GuardInstallTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve();self.project=self.root/'project';self.home=self.root/'home'
        self.journal=Journal(self.root/'state');self.addCleanup(self.journal.close)
        for provider in ('claude','codex'):
            skill=self.project/'integrations'/provider/'skills/nativepeers/SKILL.md';skill.parent.mkdir(parents=True);skill.write_text('fixture skill\n')
        (self.project/'bin').mkdir()
        for name in ('npeer','npeer-hook'):(self.project/'bin'/name).write_text('#!/bin/sh\n')
        root=patch('nativepeers.install.ROOT',self.project);root.start();self.addCleanup(root.stop)
        self.installer=Installer(self.journal,home=self.home,native=False);self.unrelated={'type':'command','command':'/usr/bin/true'}
        for provider,value in (('codex',{'hooks':{'UserPromptSubmit':[{'hooks':[self.unrelated]}]},'retained':True}),
                               ('claude',{'permissions':{'allow':['Bash(ls)']},'hooks':{'Stop':[{'hooks':[self.unrelated]}]}})):
            target=self.installer.targets[provider];target.parent.mkdir(parents=True);target.write_text(json.dumps(value))
        self.original={path:json.loads(path.read_text()) for path in self.installer.targets.values()}

    def contents(self):return {path:path.read_text() for path in self.installer.targets.values()}

    async def test_owned_user_prompt_hooks_install_idempotently_and_uninstall_only_owned_entries(self):
        first=await self.installer.install()
        codex,claude=(json.loads(self.installer.targets[p].read_text()) for p in ('codex','claude'))
        self.assertEqual(list(codex['hooks']),['UserPromptSubmit','SessionStart','SessionEnd'])
        self.assertEqual(codex['hooks']['UserPromptSubmit'],[{'hooks':[self.unrelated]},self.installer.desired_hooks('codex')['UserPromptSubmit']])
        self.assertEqual(set(claude['hooks']),{'Stop','SessionStart','SessionEnd','UserPromptSubmit','PreToolUse'})
        self.assertEqual(claude['permissions'],{'allow':['Bash(ls)']})
        snapshot=json.loads((Path(first['backup'])/'codex-owned-blocks.json').read_text())
        self.assertEqual((snapshot['changed_events'],snapshot['before_hooks']),(['SessionStart','SessionEnd','UserPromptSubmit'],{'UserPromptSubmit':[{'hooks':[self.unrelated]}]}))
        guard_checks=[c for c in _hooks(self.installer,'codex',self.installer.targets['codex']) if c.get('event')=='UserPromptSubmit']
        self.assertEqual([(c['state'],c['guard'],c['live_verified']) for c in guard_checks],[('PRESENT','passive-configuration-only',False)])
        installed=self.contents()
        self.assertFalse((await self.installer.install())['changed']);self.assertEqual(self.contents(),installed)
        planned=await self.installer.uninstall(dry_run=True)
        self.assertEqual({op['operation'] for op in planned['operations'] if op['operation'].endswith('hooks')},{'remove-owned-hooks'})
        self.assertEqual(self.contents(),installed)
        await self.installer.uninstall()
        self.assertEqual({path:json.loads(path.read_text()) for path in self.installer.targets.values()},self.original)

    async def test_trust_follows_desired_codex_hooks_and_preserves_disabled_state(self):
        installer=Installer(self.journal,home=self.home,native=True);installer.config.write_text('[hooks.state.unrelated]\nenabled=false\n[hooks.state.owned-UserPromptSubmit]\nenabled=false\n')
        calls=[];owned=['owned-'+event for event in installer.desired_hooks('codex')]
        def listing(keys):
            return [{'key':key,'currentHash':'hash-'+key,'sourcePath':str(installer.targets['codex']),'command':installer.command_for('codex')} for key in keys]+\
                   [{'key':'unrelated','currentHash':'other','sourcePath':str(installer.targets['codex']),'command':'/usr/bin/true'}]
        hooks=listing(owned)
        class Server:
            def __init__(server,*args,**kwargs):pass
            async def start(server):pass
            async def close(server):pass
            async def call(server,method,params):
                calls.append((method,params))
                if method=='config/read':return {'layers':[{'name':{'type':'user','file':str(installer.config)},'version':'v1'}]}
                return {'data':[{'hooks':hooks}]} if method=='hooks/list' else {}
        with patch('nativepeers.install.AppServer',Server):
            self.assertEqual(await installer.trust(),{key:'hash-'+key for key in owned})
            written=[params for method,params in calls if method=='config/batchWrite'][0]['edits'][0]['value']
            self.assertEqual((written['unrelated'],written['owned-UserPromptSubmit']),({'enabled':False},{'enabled':False,'trusted_hash':'hash-owned-UserPromptSubmit'}))
            hooks=listing(owned[:2])
            with self.assertRaises(PeerError) as error:await installer.trust()
        self.assertEqual((len(owned),error.exception.code),(3,'CAPABILITY_MISMATCH'))


if __name__=='__main__':unittest.main()
