"""Sacrificial PTY harness for interactive Claude Code gate runs (test-only).

A disposable interactive `claude` runs in a temporary HOME seeded so that
onboarding, folder trust and the custom-key question do not block, with
ANTHROPIC_BASE_URL at the tripwire and a dummy API key, exactly as the
headless harness. The screen is reconstructed with pyte (a VT100 emulator),
which must be importable by the interpreter that runs this file; it is a
local test tool and is not a NativePeers dependency.

Only test sessions are ever driven: this harness never attaches to, types
into or reads another terminal.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import select
import signal
import struct
import subprocess
import tempfile
import termios
import threading
import time
from pathlib import Path

import pyte

from harness import CLAUDE, DUMMY_KEY
from tripwire import Tripwire

KEYS = {'enter': '\r', 'esc': '\x1b', 'down': '\x1b[B', 'up': '\x1b[A', 'left': '\x1b[D', 'right': '\x1b[C',
        'tab': '\t', 'ctrl+x': '\x18', 'ctrl+o': '\x0f', 'ctrl+c': '\x03', 'ctrl+b': '\x02', 'ctrl+t': '\x14',
        'backspace': '\x7f', 'shift+tab': '\x1b[Z'}


def seed_home(home: Path, cwd: Path, settings=None):
    home.mkdir(parents=True, exist_ok=True)
    (home/'.claude').mkdir(exist_ok=True)
    state = {'hasCompletedOnboarding': True, 'theme': 'dark', 'numStartups': 3, 'autoUpdates': False,
             'customApiKeyResponses': {'approved': [DUMMY_KEY[-20:]], 'rejected': []},
             'hasSeenTasksHint': True, 'bypassPermissionsModeAccepted': True,
             'projects': {str(cwd): {'hasTrustDialogAccepted': True, 'hasCompletedProjectOnboarding': True,
                                     'allowedTools': [], 'projectOnboardingSeenCount': 1}}}
    (home/'.claude.json').write_text(json.dumps(state))
    (home/'.claude'/'settings.json').write_text(json.dumps(settings or {}))


class Session:
    def __init__(self, plugin_dirs, *, env=None, args=(), cols=140, rows=45, label='pty', keep=None, settings=None, cwd=None, reuse=None):
        self.scratch = Path(tempfile.mkdtemp(prefix='nppty-'))
        self.home = Path(reuse.home) if reuse else self.scratch/'home'
        self.cwd = Path(reuse.cwd) if reuse else Path(cwd) if cwd else self.scratch/'work'
        self.cwd.mkdir(parents=True, exist_ok=True)
        if not reuse:seed_home(self.home, self.cwd.resolve(), settings)
        self.keep = Path(keep) if keep else None
        if self.keep:self.keep.mkdir(parents=True, exist_ok=True)
        self.label, self.cols, self.rows = label, cols, rows
        self.screen = pyte.Screen(cols, rows)
        self.stream = pyte.ByteStream(self.screen)
        self.lock = threading.Lock()
        self.raw = bytearray()
        self.wire = Tripwire(self.scratch/'tripwire.ndjson').__enter__()
        environment = {'HOME': str(self.home), 'PATH': f'{CLAUDE.parent}:/usr/bin:/bin:/usr/sbin:/sbin',
                       'ANTHROPIC_BASE_URL': self.wire.url, 'ANTHROPIC_API_KEY': DUMMY_KEY, 'TERM': 'xterm-256color',
                       'COLORTERM': 'truecolor', 'LANG': 'en_US.UTF-8', 'TMPDIR': str(self.scratch/'tmp'),
                       'DISABLE_AUTOUPDATER': '1', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1'}
        (self.scratch/'tmp').mkdir(exist_ok=True)
        environment.update(env or {})
        self.debug = self.scratch/'debug.log'
        argv = [str(CLAUDE), '--debug-file', str(self.debug),
                *[x for p in plugin_dirs for x in ('--plugin-dir', str(Path(p).resolve()))], *args]
        master, slave = os.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
        self.proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, cwd=self.cwd, env=environment,
                                     start_new_session=True, close_fds=True)
        os.close(slave)
        self.master = master
        self.alive = True
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.started = time.time()

    def _read(self):
        while self.alive:
            try:
                ready, _, _ = select.select([self.master], [], [], 0.2)
                if not ready:continue
                data = os.read(self.master, 65536)
            except OSError:
                break
            if not data:break
            with self.lock:
                self.raw.extend(data)
                self.stream.feed(data)

    def text(self):
        with self.lock:return '\n'.join(line.rstrip() for line in self.screen.display)

    def send(self, text, delay=0.02):
        for ch in text:
            os.write(self.master, ch.encode())
            time.sleep(delay)

    def key(self, *names, pause=0.25):
        for name in names:
            os.write(self.master, KEYS.get(name, name).encode())
            time.sleep(pause)

    def type_line(self, text):
        self.send(text)
        time.sleep(0.3)
        self.key('enter')

    def wait_for(self, pattern, timeout=60, poll=0.25):
        rx = re.compile(pattern)
        end = time.time() + timeout
        while time.time() < end:
            if rx.search(self.text()):return True
            if self.proc.poll() is not None:return bool(rx.search(self.text()))
            time.sleep(poll)
        return False

    def snap(self, name):
        text = self.text()
        if self.keep:(self.keep/f'{name}.txt').write_text(text + '\n')
        return text

    def hits(self):
        return list(self.wire.hits)

    def model_hits(self):
        return [h for h in self.wire.hits if h['method'] == 'POST' and h['path'].startswith('/v1/messages')]

    def owned(self):
        """Processes whose environment carries this session's temporary HOME.

        The left arrow moves a conversation into a Claude daemon with spare PTY
        hosts that outlive the terminal; they inherit the temporary HOME, which
        no unrelated process can carry.
        """
        out = subprocess.run(['ps', '-axeww', '-o', 'pid=,command='], capture_output=True, text=True).stdout
        mark = f'HOME={self.home}'
        return [int(line.split(None, 1)[0]) for line in out.splitlines() if f' {mark} ' in f' {line} ' and int(line.split(None, 1)[0]) != os.getpid()]

    def sweep(self, grace=3):
        pids = self.owned()
        for sig in (signal.SIGTERM, signal.SIGKILL):
            for pid in pids:
                try:os.kill(pid, sig)
                except ProcessLookupError:pass
            time.sleep(grace)
            pids = self.owned()
            if not pids:break
        self.swept = pids

    def close(self, grace=5):
        if self.proc.poll() is None:
            try:
                self.key('ctrl+c');time.sleep(0.4);self.key('ctrl+c')
                self.proc.wait(grace)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGTERM)
                try:self.proc.wait(grace)
                except subprocess.TimeoutExpired:os.killpg(self.proc.pid, signal.SIGKILL)
        self.sweep()
        self.alive = False
        self.wire.__exit__(None, None, None)
        try:os.close(self.master)
        except OSError:pass
        if self.keep:
            for name in ('debug.log', 'tripwire.ndjson'):
                if (self.scratch/name).exists():(self.keep/name).write_bytes((self.scratch/name).read_bytes())
            (self.keep/'raw.bin').write_bytes(bytes(self.raw))
            (self.keep/'summary.json').write_text(json.dumps({'label': self.label, 'hits': len(self.wire.hits),
                'model_hits': len(self.model_hits()), 'paths': sorted({h['path'] for h in self.wire.hits}),
                'elapsed_s': round(time.time() - self.started, 1)}, indent=1))
