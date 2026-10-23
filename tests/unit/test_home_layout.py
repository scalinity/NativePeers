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

    def test_registry_discovery_uses_registered_layout_not_lister_environment(self):
        import json,tempfile
        from nativepeers.journal import Journal
        from nativepeers.registry import Registry
        from nativepeers.process import identity
        from nativepeers.models import new_id
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();j=Journal(root/'state');home=Path.home()/'.claude'
            try:
                for explicit in (False,True):
                    with self.subTest(explicit=explicit),patch.dict(os.environ,{},clear=True):
                        if explicit:os.environ['CLAUDE_CONFIG_DIR']=str(home)
                        peer=Registry(j).register('claude',home,new_id(),'original',identity(os.getpid()).json(),root,{'kind':'fixture'})
                        if explicit:os.environ.pop('CLAUDE_CONFIG_DIR')
                        else:os.environ['CLAUDE_CONFIG_DIR']=str(home)
                        with patch('nativepeers.adapters.metadata.claude_sessions',return_value=[dict(sessionId=peer['peer_key']['native_session_id'],pid=os.getpid(),name='correct-layout')]) as discover:
                            listed=Registry(j).list('claude')
                        discover.assert_called_once_with(home,explicit=explicit)
                        self.assertEqual(listed[0]['title'],'correct-layout')
                        self.assertIs(listed[0]['provider_home_explicit'],explicit)
                # An unrecorded legacy intent must stay unknown.
                value=json.loads(j.one('SELECT descriptor_json FROM peer_instances ORDER BY last_seen DESC LIMIT 1')['descriptor_json'])
                value.pop('provider_home_explicit');j.db.execute('UPDATE peer_instances SET descriptor_json=? WHERE incarnation_id=?',(json.dumps(value),value['incarnation_id']))
                with patch('nativepeers.adapters.metadata.claude_sessions') as discover:Registry(j).list('claude')
                discover.assert_not_called()
            finally:j.close()
