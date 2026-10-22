import json
import os
from pathlib import Path
import tempfile
import unittest
import time
from unittest.mock import patch

from nativepeers.hooks import observe
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.registry import Registry
from nativepeers.files import atomic_json
from nativepeers.caller import bind_caller


class HookIdentityTests(unittest.TestCase):
    def test_claude_native_tool_mode_updates_editing_capability_without_rebinding(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory);journal=Journal(home/'state');owner=identity(os.getpid())
            binary=home/'.local/bin/claude';binary.parent.mkdir(parents=True);binary.symlink_to(owner.executable)
            native=new_id();registry=Registry(journal)
            peer=registry.register('claude',home,native,'parent',owner.json(),home,{'kind':'fake'},policy={'profiles':['analysis']})
            binding=journal.root/'bindings/parent';atomic_json(binding,{'state':'BOUND','kind':'peer','peer_key':peer['handle'],
                'incarnation_id':peer['incarnation_id'],'owner':owner.json(),'expires_at':time.time()+60,'grants':{'profiles':['analysis']}})
            event={'session_id':native,'hook_event_name':'PreToolUse','tool_name':'Bash','permission_mode':'dontAsk'}
            with patch('nativepeers.hooks.Path.home',return_value=home), \
                 patch('nativepeers.hooks.ancestry',return_value=[owner,owner]), \
                 patch.dict(os.environ,{'NPEER_BINDING':str(binding),'NPEER_BOOTSTRAP':''}):
                self.assertEqual(bind_caller(journal,registry).grants['profiles'],['analysis'])
                observe('claude',event,journal)
                self.assertEqual(bind_caller(journal,registry).grants['profiles'],['analysis','workspace'])
                self.assertEqual(registry.get(peer['handle'])['incarnation_id'],peer['incarnation_id'])
                observe('claude',dict(event,permission_mode='plan'),journal)
                self.assertEqual(bind_caller(journal,registry).grants['profiles'],['analysis'])
                self.assertFalse(observe('claude',dict(event,session_id=new_id()),journal)['updated'])
            journal.close()

    def test_historyless_native_hook_does_not_replace_the_live_tui_conversation(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory);journal=Journal(home/'state');owner=identity(os.getpid())
            binary=home/'.local/share/codex-mcp-manager/current/bin/codex';binary.parent.mkdir(parents=True);binary.symlink_to(owner.executable)
            native=new_id();transcript=home/'.codex/sessions'/('test-'+native+'.jsonl');transcript.parent.mkdir(parents=True)
            transcript.write_text(json.dumps({'type':'session_meta','payload':{'id':native}})+'\n')
            event={'session_id':native,'hook_event_name':'SessionStart','cwd':str(home),'transcript_path':str(transcript)}
            with patch('nativepeers.hooks.Path.home',return_value=home), \
                 patch('nativepeers.hooks.ancestry',return_value=[owner,owner]), \
                 patch('nativepeers.process.terminal_owner',return_value=True), \
                 patch('nativepeers.hooks.codex_name',return_value='Exact parent'), \
                 patch.dict(os.environ,{'NPEER_BOOTSTRAP':'','CODEX_HOME':str(home/'.codex')}):
                result=observe('codex',event,journal)
                parent=Registry(journal).get(result['registered'])
                background=dict(event,session_id=new_id(),transcript_path=None)
                self.assertFalse(observe('codex',background,journal)['registered'])
                observe('codex',dict(background,hook_event_name='SessionEnd'),journal)
                self.assertEqual(Registry(journal).get(parent['handle'])['incarnation_id'],parent['incarnation_id'])
                self.assertEqual(len(Registry(journal).list('codex')),1)
                with self.assertRaises(Exception) as error:observe('codex',dict(background,transcript_path=str(transcript)),journal)
                self.assertEqual(error.exception.code,'OWNERSHIP_CONFLICT')
            journal.close()
