import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from nativepeers import claude_mod_install as installer
from nativepeers.errors import PeerError

FAKE = r'''#!{python}
import json, os, sys
state_path = os.environ["FAKE_CLAUDE_STATE"]
state = json.load(open(state_path))
args = sys.argv[1:]
with open(state_path + ".log", "a") as log:log.write(" ".join(args) + "\n")
def save():json.dump(state, open(state_path, "w"))
if args[:3] == ["plugin", "marketplace", "list"]:print(json.dumps(state["markets"]))
elif args[:2] == ["plugin", "list"]:print(json.dumps(state["plugins"]))
elif args[:3] == ["plugin", "marketplace", "add"]:state["markets"].append({"name": "nativepeers-local", "source": "directory", "path": args[3]});save()
elif args[:3] == ["plugin", "marketplace", "remove"]:state["markets"] = [m for m in state["markets"] if m["name"] != args[3]];save()
elif args[:2] == ["plugin", "install"]:state["plugins"].append({"id": args[2], "enabled": True, "scope": "user"});save()
elif args[:2] == ["plugin", "enable"]:[p.update(enabled=True) for p in state["plugins"] if p["id"] == args[2]];save()
elif args[:2] == ["plugin", "uninstall"]:state["plugins"] = [p for p in state["plugins"] if p["id"] != args[2]];save()
else:sys.exit(2)
'''


class ClaudeModInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.claude = self.tmp/'claude'
        self.claude.write_text(FAKE.replace('{python}', sys.executable))
        self.claude.chmod(self.claude.stat().st_mode | stat.S_IEXEC)
        self.state = self.tmp/'state.json'
        self.source = self.tmp/'mod';self.source.mkdir()
        self.home = self.tmp/'home';(self.home/'.claude/plugins').mkdir(parents=True)
        (self.home/'.claude/settings.json').write_text('{"keep": true}')
        self.journal = SimpleNamespace(root=self.tmp/'npeer')
        self.write(markets=[{'name': 'other', 'source': 'github'}], plugins=[{'id': 'x@other', 'enabled': True}])
        patcher = mock.patch.dict(os.environ, {'FAKE_CLAUDE_STATE': str(self.state)});patcher.start();self.addCleanup(patcher.stop)
        root = mock.patch.object(installer, 'ROOT', self.tmp);root.start();self.addCleanup(root.stop)

    def write(self, **state):
        self.state.write_text(json.dumps(state))

    def run_apply(self, **kwargs):
        return installer.apply(self.journal, executable=self.claude, source=self.source, home=self.home, **kwargs)

    def log(self):
        path = Path(str(self.state)+'.log')
        return path.read_text().splitlines() if path.exists() else []

    def test_dry_run_plans_without_changing_anything(self):
        value = self.run_apply(dry_run=True)
        self.assertEqual(value['operations'], [f'claude plugin marketplace add {self.source}', 'claude plugin install np@nativepeers-local --scope user'])
        self.assertFalse(any(line.startswith(('plugin marketplace add', 'plugin install')) for line in self.log()))
        self.assertFalse((self.journal.root/'install/claude-mod.json').exists())

    def test_install_backs_up_records_a_manifest_and_repeats_as_zero_operations(self):
        first = self.run_apply()
        self.assertEqual(len(first['applied']), 2)
        manifest = json.loads((self.journal.root/'install/claude-mod.json').read_text())
        self.assertIn('claude plugin uninstall np@nativepeers-local --scope user', manifest['uninstall'])
        self.assertTrue(any(path.endswith('.claude/settings.json') for path in first['backups']))
        self.assertEqual(json.loads((self.home/'.claude/settings.json').read_text()), {'keep': True})
        second = self.run_apply()
        self.assertEqual(second['operations'], [])
        self.assertNotIn('applied', second)

    def test_a_disabled_install_is_enabled_not_reinstalled(self):
        self.write(markets=[{'name': 'nativepeers-local', 'source': 'directory', 'path': str(self.source)}], plugins=[{'id': 'np@nativepeers-local', 'enabled': False}])
        self.assertEqual(self.run_apply(dry_run=True)['operations'], ['claude plugin enable np@nativepeers-local --scope user'])

    def test_a_marketplace_of_that_name_elsewhere_is_refused(self):
        self.write(markets=[{'name': 'nativepeers-local', 'source': 'directory', 'path': str(self.tmp/'elsewhere')}], plugins=[])
        with self.assertRaises(PeerError) as caught:self.run_apply()
        self.assertEqual(caught.exception.code, 'OWNERSHIP_CONFLICT')

    def test_uninstall_removes_only_what_the_installer_owns(self):
        self.run_apply()
        removed = self.run_apply(remove=True)
        self.assertEqual(removed['applied'], ['claude plugin uninstall np@nativepeers-local --scope user', 'claude plugin marketplace remove nativepeers-local'])
        state = json.loads(self.state.read_text())
        self.assertEqual([m['name'] for m in state['markets']], ['other'])
        self.assertEqual([p['id'] for p in state['plugins']], ['x@other'])


if __name__ == '__main__':
    unittest.main()
