"""Desktop-hosted Claude Code engines as mirror-only Claude owners (docs/CLAUDE_LINEAGE_MOD.md §6.7)."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from nativepeers import desktop_host
from nativepeers.caller import native_binaries
from nativepeers.completion_delivery import readiness
from nativepeers.errors import PeerError
from nativepeers.hooks import observe
from nativepeers.journal import Journal
from nativepeers.process import identity
from nativepeers.registry import Registry

NATIVE = '00000000-0000-4000-8000-0000000000d1'


def done(code=0, out=b''):
    return subprocess.CompletedProcess([], code, out, b'')


class DesktopHostTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve();self.user = self.root/'user'
        self.journal = Journal(self.root/'state');self.addCleanup(self.journal.close)
        (Path(self.journal.root)/'compat').mkdir(parents=True, exist_ok=True)
        app = self.user/'Library/Application Support/Claude/claude-code/2.1.309/b1/claude.app'
        (app/'Contents/MacOS').mkdir(parents=True);(app/'Contents/Info.plist').write_text('<plist/>')
        self.engine = app/'Contents/MacOS/claude';self.engine.write_bytes(b'desktop engine 2.1.309');self.engine.chmod(0o755)
        home = patch('nativepeers.desktop_host.Path.home', return_value=self.user);home.start();self.addCleanup(home.stop)

    def checks(self, signed=0, shown=b'2.1.309 (Claude Code)\n'):
        return patch('nativepeers.desktop_host.subprocess.run', side_effect=lambda argv, **kw: done(signed) if argv[0].endswith('codesign') else done(0, shown))

    def owner(self, executable):
        return dict(identity(os.getpid()).json(), executable=str(executable))

    def process(self, executable):
        value = self.owner(executable)
        return type('Process', (), {'json': lambda _: value})()

    def test_only_the_desktop_apps_own_engine_layout_is_a_candidate(self):
        self.assertTrue(desktop_host.candidate(self.engine))
        for path in (self.user/'.local/bin/claude', self.engine.parent/'other', self.user/'Library/Application Support/Claude/claude-code/claude',
                     self.root/'elsewhere/claude-code/2.1.309/b1/claude.app/Contents/MacOS/claude'):
            self.assertFalse(desktop_host.candidate(path), path)

    def test_first_use_approves_a_signed_engine_once_and_binding_then_matches_it_by_path(self):
        with self.checks() as run:
            record = desktop_host.approve_nearest(self.journal, [self.owner(self.engine)], set())
            self.assertEqual((record['path'], record['checks']), (str(self.engine), ['vendor-signature', 'supported-version']))
            self.assertEqual(run.call_count, 2)
            desktop_host.approve_nearest(self.journal, [self.owner(self.engine)], set());self.assertEqual(run.call_count, 2)
        self.assertEqual(desktop_host.paths(self.journal), {str(self.engine)})
        self.assertEqual(native_binaries(self.journal)[self.engine], 'claude')
        self.engine.write_bytes(b'replaced');self.assertEqual(desktop_host.paths(self.journal), set())

    def test_a_definite_refusal_is_recorded_and_not_retried_for_the_same_bytes(self):
        for signed, shown in ((1, b'2.1.309 (Claude Code)'), (0, b'2.0.99 (Claude Code)')):
            with self.subTest(signed=signed, shown=shown):
                self.engine.write_bytes(b'engine ' + shown)
                with self.checks(signed, shown) as run:
                    self.assertIsNone(desktop_host.approve_nearest(self.journal, [self.owner(self.engine)], set()))
                    self.assertIsNone(desktop_host.approve_nearest(self.journal, [self.owner(self.engine)], set()))
                    self.assertEqual(run.call_count, 2)
                self.assertEqual(desktop_host.paths(self.journal), set())

    def test_a_nearer_cli_owner_is_never_replaced_by_an_outer_desktop_engine(self):
        cli = str(self.user/'.local/bin/claude')
        with self.checks() as run:
            self.assertIsNone(desktop_host.approve_nearest(self.journal, [self.owner(cli), self.owner(self.engine)], {cli}))
        run.assert_not_called()

    def test_a_desktop_session_without_an_inbox_registers_as_a_mirror_only_parent(self):
        event = {'hook_event_name': 'SessionStart', 'session_id': NATIVE, 'cwd': str(self.root)}
        with self.checks(), patch('nativepeers.hooks.Path.home', return_value=self.user), patch('nativepeers.registry.matches', return_value=True), patch('nativepeers.hooks.claude_name', return_value=None), \
             patch('nativepeers.hooks.ancestry', return_value=[self.process(self.engine), self.process(self.engine)]), \
             patch.dict(os.environ, {'NPEER_BOOTSTRAP': '', 'CLAUDE_CONFIG_DIR': str(self.root/'claude-home'), 'CLAUDE_CODE_MESSAGING_SOCKET': ''}):
            handle = observe('claude', event, self.journal)['registered']
            peer = Registry(self.journal).get(handle)
            self.assertEqual((peer['owner_kind'], peer['transport'], peer['capabilities']), ('interactive-standalone', {'kind': 'none'}, {'mirror': True}))
            self.assertEqual(readiness(self.journal, handle)['state'], 'MIRROR_ONLY')

    def test_a_cli_session_without_an_inbox_is_still_refused(self):
        cli = (self.user/'.local/bin/claude').resolve()
        event = {'hook_event_name': 'SessionStart', 'session_id': NATIVE, 'cwd': str(self.root)}
        with patch('nativepeers.hooks.Path.home', return_value=self.user), patch('nativepeers.registry.matches', return_value=True), patch('nativepeers.hooks.claude_name', return_value=None), \
             patch('nativepeers.hooks.ancestry', return_value=[self.process(cli), self.process(cli)]), \
             patch.dict(os.environ, {'NPEER_BOOTSTRAP': '', 'CLAUDE_CONFIG_DIR': str(self.root/'claude-home'), 'CLAUDE_CODE_MESSAGING_SOCKET': ''}):
            with self.assertRaises(PeerError) as error:observe('claude', event, self.journal)
        self.assertEqual(error.exception.code, 'UNSUPPORTED_TRANSPORT')


if __name__ == '__main__':
    unittest.main()
