"""Core-only native shell environments retain only the owned worker binding."""
import os
from pathlib import Path
import tempfile
import unittest

from nativepeers.journal import Journal
from nativepeers.profiles import codex_config


class WorkerShellBindingTests(unittest.TestCase):
    def test_child_overlay_supplies_exact_binding_and_preserves_existing_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);home=root/'provider';home.mkdir()
            config=home/'config.toml'
            raw='[shell_environment_policy]\ninherit="core"\n[shell_environment_policy.set]\nKEEP="existing"\nNPEER_BINDING="parent-binding"\n'
            config.write_text(raw);journal=Journal(root/'state')
            try:
                binding=journal.root/'bindings/owned-worker'
                overlay=codex_config(journal,binding,'analysis',home=home)
                selected=overlay['shell_environment_policy.set']
                self.assertEqual(selected['KEEP'],'existing')
                self.assertEqual(selected['NPEER_BOOTSTRAP'],str(binding))
                self.assertEqual(selected['NPEER_HOME'],str(journal.root))
                self.assertEqual(selected['NPEER_BINDING'],'')
                self.assertNotIn('PYTHONPATH',selected)
                self.assertNotIn('shell_environment_policy.inherit',overlay)
                self.assertEqual(config.read_text(),raw)
                self.assertNotIn('sandbox',overlay)
            finally:journal.close()
