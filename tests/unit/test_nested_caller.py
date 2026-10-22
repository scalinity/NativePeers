import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from nativepeers.caller import bind_caller


class NestedCallerTests(unittest.TestCase):
    def owner(self,provider,pid):
        executable=(Path.home()/{
            'antigravity':'.local/bin/agy','codex':'.local/share/codex-mcp-manager/current/bin/codex',
            'claude':'.local/bin/claude'}[provider]).resolve()
        return {'pid':pid,'ppid':1,'pgid':pid,'uid':os.getuid(),'start':str(pid),'executable':str(executable)}

    def invoke(self,owners,peers):
        registry=Mock();registry.list.side_effect=lambda provider:peers.get(provider,[])
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
