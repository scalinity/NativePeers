"""Desktop-hosted Claude Code engines as mirror-only Claude owners (docs/CLAUDE_LINEAGE_MOD.md §6.7)."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from nativepeers import desktop_host
from nativepeers.caller import native_binaries, nearest_native
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
        self.engine = self.bundle('b1', b'desktop engine 2.1.309')
        home = patch('nativepeers.desktop_host.Path.home', return_value=self.user);home.start();self.addCleanup(home.stop)

    def bundle(self, build, content):
        app = self.user/'Library/Application Support/Claude/claude-code/2.1.309'/build/'claude.app'
        (app/'Contents/MacOS').mkdir(parents=True);(app/'Contents/Info.plist').write_text('<plist/>')
        engine = app/'Contents/MacOS/claude';engine.write_bytes(content);engine.chmod(0o755)
        return engine

    def checks(self, signed=0, shown=b'2.1.309 (Claude Code)\n', shown_code=0):
        return patch('nativepeers.desktop_host.subprocess.run',
                     side_effect=lambda argv, **kw: done(signed) if argv[0].endswith('codesign') else done(shown_code, shown))

    def owner(self, executable):
        return dict(identity(os.getpid()).json(), executable=str(executable))

    def process(self, executable):
        value = self.owner(executable)
        return type('Process', (), {'json': lambda _: value})()

    def hook(self, *chain, approve_desktop=False, socket=''):
        """SessionStart as the hook (or, with approve_desktop, the np bridge) sees it below the given processes."""
        event = {'hook_event_name': 'SessionStart', 'session_id': NATIVE, 'cwd': str(self.root)}
        with patch('nativepeers.hooks.Path.home', return_value=self.user), patch('nativepeers.registry.matches', return_value=True), \
             patch('nativepeers.hooks.claude_name', return_value=None), patch('nativepeers.hooks.ancestry', return_value=[self.process(chain[0]), *map(self.process, chain)]), \
             patch.dict(os.environ, {'NPEER_BOOTSTRAP': '', 'CLAUDE_CONFIG_DIR': str(self.root/'claude-home'), 'CLAUDE_CODE_MESSAGING_SOCKET': socket}):
            return observe('claude', event, self.journal, approve_desktop=approve_desktop)

    def test_only_the_desktop_apps_own_engine_layout_is_a_candidate(self):
        self.assertTrue(desktop_host.candidate(self.engine))
        for path in (self.user/'.local/bin/claude', self.engine.parent/'other', self.user/'Library/Application Support/Claude/claude-code/claude',
                     self.root/'elsewhere/claude-code/2.1.309/b1/claude.app/Contents/MacOS/claude'):
            self.assertFalse(desktop_host.candidate(path), path)

    def test_first_use_approves_a_signed_engine_once_and_binding_then_matches_it_by_path(self):
        with self.checks() as run:
            record = desktop_host.approve(self.journal, self.engine)
            self.assertEqual((record['path'], record['checks']), (str(self.engine), ['vendor-signature', 'supported-version']))
            desktop_host.approve(self.journal, self.engine);self.assertEqual(run.call_count, 2)
        self.assertTrue(desktop_host.approved(self.journal, self.engine))
        self.assertEqual(native_binaries(self.journal)[self.engine], 'claude')
        self.engine.write_bytes(b'replaced')
        self.assertFalse(desktop_host.approved(self.journal, self.engine));self.assertEqual(desktop_host.paths(self.journal), set())

    def test_identical_bytes_at_another_path_get_their_own_approval(self):
        second = self.bundle('b2', b'desktop engine 2.1.309')
        with self.checks() as run:
            desktop_host.approve(self.journal, self.engine);desktop_host.approve(self.journal, second)
        self.assertEqual(run.call_count, 4)
        self.assertEqual(desktop_host.paths(self.journal), {str(self.engine), str(second)})

    def test_only_a_definite_failure_is_recorded_and_an_unsuccessful_check_is_retried(self):
        for signed, shown in ((1, b'2.1.309 (Claude Code)'), (0, b'2.0.99 (Claude Code)')):
            with self.subTest(signed=signed, shown=shown):
                self.engine.write_bytes(b'engine ' + shown)
                with self.checks(signed, shown) as run:
                    self.assertIsNone(desktop_host.approve(self.journal, self.engine))
                    self.assertIsNone(desktop_host.approve(self.journal, self.engine))
                    self.assertEqual(run.call_count, 2)
        self.engine.write_bytes(b'engine sandbox failure')
        with self.checks(shown=b'', shown_code=71) as run:
            self.assertIsNone(desktop_host.approve(self.journal, self.engine));self.assertIsNone(desktop_host.approve(self.journal, self.engine))
            self.assertEqual(run.call_count, 4)
        with self.checks():
            self.assertIsNotNone(desktop_host.approve(self.journal, self.engine))

    def test_an_unapproved_desktop_engine_is_a_boundary_never_searched_past(self):
        outer = self.bundle('b2', b'approved outer engine')
        with self.checks():desktop_host.approve(self.journal, outer)
        # The nearer engine has no approval: nothing farther up may own its session or bind its tools.
        self.assertEqual(nearest_native([self.owner(self.engine), self.owner(outer)], journal=self.journal)['executable'], str(self.engine))
        with patch('nativepeers.desktop_host.subprocess.run') as run, self.assertRaises(PeerError) as error:
            self.hook(self.engine, outer)
        self.assertEqual(error.exception.code, 'CALLER_UNBOUND');run.assert_not_called()
        self.assertEqual(Registry(self.journal).for_native('claude', NATIVE), [])

    def test_the_bridge_approves_on_first_use_and_registers_a_mirror_only_parent(self):
        with patch('nativepeers.desktop_host.subprocess.run') as run, self.assertRaises(PeerError):
            self.hook(self.engine)
        run.assert_not_called()
        with self.checks(), patch('nativepeers.registry.matches', return_value=True):
            handle = self.hook(self.engine, approve_desktop=True)['registered']
            peer = Registry(self.journal).get(handle)
            self.assertEqual((peer['owner_kind'], peer['transport'], peer['capabilities']), ('interactive-standalone', {'kind': 'none'}, {'mirror': True}))
            self.assertEqual(readiness(self.journal, handle)['state'], 'MIRROR_ONLY')
        with patch('nativepeers.desktop_host.subprocess.run') as run:
            self.assertTrue(self.hook(self.engine)['registered'])
        run.assert_not_called()

    def test_the_bridge_registers_only_below_an_unapproved_desktop_engine(self):
        from nativepeers import bridge_backend
        cli = (self.user/'.local/bin/claude').resolve()
        for chain, called in (([cli], False), ([self.engine], True)):
            with self.subTest(chain=chain[0].name), patch('nativepeers.bridge_backend.ancestry', return_value=[self.process(chain[0]), *map(self.process, chain)]), \
                 patch('nativepeers.hooks.observe') as register:
                bridge_backend.register_desktop(self.journal, NATIVE)
            self.assertEqual(register.called, called)
            if called:self.assertTrue(register.call_args.kwargs['approve_desktop'])
        with self.checks():desktop_host.approve(self.journal, self.engine)
        with patch('nativepeers.bridge_backend.ancestry', return_value=[self.process(self.engine)] * 2), patch('nativepeers.hooks.observe') as register:
            bridge_backend.register_desktop(self.journal, NATIVE)
        register.assert_not_called()

    def test_a_cli_session_without_an_inbox_is_still_refused(self):
        with self.assertRaises(PeerError) as error:self.hook((self.user/'.local/bin/claude').resolve())
        self.assertEqual(error.exception.code, 'UNSUPPORTED_TRANSPORT')


if __name__ == '__main__':
    unittest.main()
