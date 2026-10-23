import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from nativepeers.launch import apply_home, home_environment
from nativepeers.adapters.metadata import claude_sessions


class HomeLayoutTests(unittest.TestCase):
    def test_implicit_and_explicit_layout_survive_ambient_changes(self):
        with patch('pathlib.Path.home', return_value=Path('/fixture')):
            for home, explicit in [(Path('/fixture/.claude'), False),
                                   (Path('/fixture/.claude'), True),
                                   (Path('/alternate'), True)]:
                with self.subTest(home=home, explicit=explicit):
                    # Recorded launch layout wins over an unrelated current shell.
                    env=apply_home('claude',home,{'CLAUDE_CONFIG_DIR':'/wrong','HOME':'/fixture'},explicit=explicit)
                    self.assertEqual(env.get('CLAUDE_CONFIG_DIR'),str(home) if explicit else None)
                    self.assertEqual(home_environment('claude',home,explicit=explicit),
                                     {'CLAUDE_CONFIG_DIR':str(home)} if explicit else {})
                    with patch('nativepeers.adapters.metadata.subprocess.run',return_value=Mock(returncode=0,stdout='[]')) as run:
                        claude_sessions(home,explicit=explicit)
                    self.assertEqual(run.call_args.kwargs['env'].get('CLAUDE_CONFIG_DIR'),str(home) if explicit else None)

    def test_initial_selection_distinguishes_explicit_default(self):
        with patch('pathlib.Path.home',return_value=Path('/fixture')),patch.dict(os.environ,{},clear=True):
            self.assertEqual(home_environment('claude','/fixture/.claude'),{})
            os.environ['CLAUDE_CONFIG_DIR']='/fixture/.claude'
            self.assertEqual(home_environment('claude','/fixture/.claude'),{'CLAUDE_CONFIG_DIR':'/fixture/.claude'})
