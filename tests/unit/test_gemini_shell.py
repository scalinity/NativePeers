import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class GeminiShellTests(unittest.TestCase):
    def invoke(self, arguments):
        with tempfile.TemporaryDirectory() as directory:
            # Rebind installation-specific paths only in the offline fixture.
            shell = (ROOT / 'integrations/zsh/gemini.zsh').read_text()
            installed = ('PYTHONPATH=/Users/danny/Documents/Tools/NativePeers '
                         '/Users/danny/Documents/Tools/NativePeers/.venv/bin/python')
            self.assertEqual(shell.count(installed), 1)
            fixture = Path(directory) / 'gemini.zsh'
            fixture.write_text(shell.replace(
                installed, f'PYTHONPATH={shlex.quote(str(ROOT))} {shlex.quote(sys.executable)}'))
            stub = Path(directory) / 'agy'
            stub.write_text('#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n')
            stub.chmod(0o755)
            return subprocess.run(['zsh', '-f', '-c', 'source "$1"; shift; gemini "$@"',
                                   'fixture', str(fixture), *arguments],
                                  env={**os.environ, 'HOME':directory,'PATH': directory + os.pathsep + os.environ['PATH']},
                                  capture_output=True, text=True, timeout=10)

    def test_bare_passthrough_and_continue_use_native_bypass(self):
        for arguments, expected in [([], []), (['--help'], ['--help']),
                                    (['-c', '--model', 'gemini-4-argon-high'],
                                     ['--continue', '--model', 'gemini-4-argon-high']),
                                    (['--continue'], ['--continue'])]:
            with self.subTest(arguments=arguments):
                result = self.invoke(arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), ['--dangerously-skip-permissions', *expected])

    def test_quoted_and_unquoted_names_explain_the_native_limit_without_submitting_a_prompt(self):
        for names in [['Argon Review'], ['Argon', 'Review']]:
            result = self.invoke(['-n', *names, '--model', 'gemini-4-argon-high'])
            self.assertEqual(result.returncode,2);self.assertEqual(result.stdout,'')
            self.assertIn('/rename Argon Review',result.stderr)
            self.assertNotIn('--model',result.stderr)

    def test_resume_id_or_native_picker_without_guessing_a_title(self):
        native = 'd6fc7a69-5b75-40ae-a417-e434bad413b1'
        self.assertEqual(json.loads(self.invoke(['-r', native]).stdout),
                         ['--dangerously-skip-permissions', '--conversation', native])
        picker=self.invoke(['-r'])
        self.assertEqual(json.loads(picker.stdout),['--dangerously-skip-permissions'])
        self.assertIn('enter /resume',picker.stderr)
        missing = self.invoke(['-r', 'ambiguous', 'title'])
        self.assertEqual(missing.returncode, 2, missing.stderr)
        self.assertEqual(missing.stdout, '')
        self.assertIn('No Antigravity conversation has that exact native title', missing.stderr)

    def test_empty_and_multiline_names_do_not_launch_a_native_process(self):
        for arguments in [['-n'], ['--name', ' '], ['-n', '--model', 'gemini-4-argon-high'],
                          ['-n', 'line\nother']]:
            with self.subTest(arguments=arguments):
                result = self.invoke(arguments)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
