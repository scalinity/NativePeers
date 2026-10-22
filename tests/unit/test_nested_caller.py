import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from nativepeers.caller import bind_caller
from nativepeers.mcp_server import Facade


class NestedCallerTests(unittest.TestCase):
    def owner(self,provider,pid):
        executable=(Path.home()/{
            'antigravity':'.local/bin/agy','codex':'.local/share/codex-mcp-manager/current/bin/codex',
            'claude':'.local/bin/claude'}[provider]).resolve()
        return {'pid':pid,'ppid':1,'pgid':pid,'uid':os.getuid(),'start':str(pid),'executable':str(executable)}

    def invoke(self,owners,peers):
        registry=Mock();registry.list.side_effect=lambda provider:peers.get(provider,[])
        registry.for_owner.side_effect=lambda provider,owner:[p for p in peers.get(provider,[]) if p['owner']==owner]
        registry.for_native.side_effect=lambda provider,native:[p for p in peers.get(provider,[]) if p['peer_key']['native_session_id']==native]
        with patch('nativepeers.caller.ancestry',return_value=[SimpleNamespace(json=lambda p=p:p) for p in owners]), \
             patch.dict(os.environ,{'NPEER_BINDING':'','NPEER_BOOTSTRAP':'','CODEX_THREAD_ID':'codex-native'}):
            return bind_caller(Mock(),registry,allow_external=False)

    def peer(self,owner,provider):
        return {'owner':owner,'handle':provider+'-handle','incarnation_id':'incarnation','peer_key':{'native_session_id':'codex-native'}}

    def test_codex_child_does_not_borrow_outer_antigravity_identity(self):
        codex=self.owner('codex',1);agy=self.owner('antigravity',2)
        caller=self.invoke([codex,agy],{'codex':[self.peer(codex,'codex')],'antigravity':[self.peer(agy,'antigravity')]})
        self.assertEqual(caller.key,'codex-handle')

    def test_unregistered_antigravity_child_cannot_borrow_outer_codex_identity(self):
        agy=self.owner('antigravity',1);codex=self.owner('codex',2)
        with self.assertRaises(Exception) as error:self.invoke([agy,codex],{'codex':[self.peer(codex,'codex')]})
        self.assertEqual(error.exception.code,'CALLER_UNBOUND')

    def test_registered_antigravity_child_uses_its_exact_owner(self):
        agy=self.owner('antigravity',1);codex=self.owner('codex',2)
        caller=self.invoke([agy,codex],{'codex':[self.peer(codex,'codex')],'antigravity':[self.peer(agy,'antigravity')]})
        self.assertEqual(caller.key,'antigravity-handle')

    def test_claude_without_a_binding_cannot_borrow_outer_codex_identity(self):
        claude=self.owner('claude',1);codex=self.owner('codex',2)
        with self.assertRaises(Exception) as error:self.invoke([claude,codex],{'codex':[self.peer(codex,'codex')]})
        self.assertEqual(error.exception.code,'CALLER_UNBOUND')

    def test_codex_child_cannot_borrow_outer_codex_thread_environment(self):
        child=self.owner('codex',1);outer=self.owner('codex',2)
        with self.assertRaises(Exception) as error:self.invoke([child,outer],{'codex':[self.peer(outer,'codex')]})
        self.assertEqual(error.exception.code,'CALLER_UNBOUND')

    def test_ambient_interactive_binding_rejects_an_enclosing_native_owner(self):
        outer=self.owner('claude',2);inner=self.owner('antigravity',1)
        peer=dict(self.peer(outer,'claude'),owner_kind='interactive-standalone',policy={'profiles':['workspace']})
        registry=Mock();registry.verify.return_value=peer
        binding={'kind':'peer','peer_key':peer['handle'],'incarnation_id':peer['incarnation_id'],'grants':{}}
        for owners,accepted in [([inner,outer],False),([outer],True)]:
            with self.subTest(accepted=accepted), \
                 patch('nativepeers.caller.read_binding',return_value=binding), \
                 patch('nativepeers.caller.ancestry',return_value=[SimpleNamespace(json=lambda p=p:p) for p in owners]), \
                 patch.dict(os.environ,{'NPEER_BINDING':'/unused','NPEER_BOOTSTRAP':''}):
                if accepted:self.assertEqual(bind_caller(Mock(),registry).key,peer['handle'])
                else:
                    with self.assertRaises(Exception) as error:bind_caller(Mock(),registry)
                    self.assertEqual(error.exception.code,'CALLER_UNBOUND')
                    with patch('nativepeers.mcp_server.Registry',return_value=registry):
                        result=asyncio.run(Facade(Mock(),None).call(None,SimpleNamespace(name='peer_send',arguments={'target':'x','text':'no','client_request_id':'x'})))
                    self.assertTrue(result.is_error)
