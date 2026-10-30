"""The np mod's compatibility verdict for a Claude Code build, with no model input.

`runtime-check --provider claude` runs it beside the existing runtime checks:
`claude plugin validate` on the np mod and the probe mod, then one headless
run of the probe in a temporary HOME with ANTHROPIC_BASE_URL at a local
tripwire that refuses and counts every request. The verdict passes only when
the tripwire saw nothing and the probe shows a shadow answered by its hooks,
a replay-only tool call, and a fault contained by .catch.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .profiles import ROOT

MOD = ROOT/'integrations/claude/mod/np'
PROBE = ROOT/'integrations/claude/mod-check'
DUMMY = 'sk-ant-api03-nativepeers-tripwire-dummy-0000000000'


class _Tripwire:
    def __init__(self):
        self.hits = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def answer(self):
                length = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(length) if length else b''
                try:model = json.loads(body).get('model')
                except (ValueError, AttributeError):model = None
                owner.hits.append({'path': self.path.split('?')[0], 'model': model})
                self.send_response(400);self.send_header('Content-Length', '2');self.end_headers();self.wfile.write(b'{}')
            do_GET = do_POST = answer

            def log_message(self, *args):pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown();self.server.server_close()


def _validate(executable, folder):
    result = subprocess.run([str(executable), 'plugin', 'validate', str(folder)], capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    return dict(ok=result.returncode == 0, tail=(result.stdout + result.stderr).strip().splitlines()[-1:] if result.returncode else [])


def check(executable, *, timeout=120):
    executable = Path(executable)
    value = dict(component='np-mod', validate=dict(np=_validate(executable, MOD), probe=_validate(executable, PROBE)))
    wire = _Tripwire()
    scratch = Path(tempfile.mkdtemp(prefix='np-modcheck-'))
    try:
        (scratch/'home').mkdir();(scratch/'tmp').mkdir()
        environment = {'HOME': str(scratch/'home'), 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'TMPDIR': str(scratch/'tmp'), 'LANG': 'en_US.UTF-8',
                       'ANTHROPIC_BASE_URL': wire.url, 'ANTHROPIC_API_KEY': DUMMY, 'DISABLE_AUTOUPDATER': '1', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1'}
        try:
            run = subprocess.run([str(executable), '-p', 'check', '--output-format', 'stream-json', '--verbose', '--plugin-dir', str(PROBE)],
                                 env=environment, cwd=scratch, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
            lines = run.stdout.splitlines()
        except subprocess.TimeoutExpired:
            lines = []
        events = []
        for line in lines:
            try:events.append(json.loads(line))
            except ValueError:pass
        result = next((e for e in reversed(events) if e.get('type') == 'result'), {})
        text = str(result.get('result') or '')
        version = next((e.get('claude_code_version') for e in events if e.get('type') == 'system' and e.get('subtype') == 'init'), None)
        harness = dict(version=version, tripwire_hits=len(wire.hits), tripwire_models=sorted({str(h['model']) for h in wire.hits}),
                       total_cost_usd=result.get('total_cost_usd'), model_usage=result.get('modelUsage'), result=text[:400])
    finally:
        wire.close()
    reasons = []
    if not value['validate']['np']['ok']:reasons.append('claude plugin validate refused the np mod')
    if not value['validate']['probe']['ok']:reasons.append('claude plugin validate refused the probe mod')
    if harness['tripwire_hits']:reasons.append(f"{harness['tripwire_hits']} model request(s) reached the tripwire")
    for needed, why in (('step0:np-mirror-check', 'the probe shadow step was not answered on its unserved model'),
                        ('replay', 'the replay-only tool call did not run'), ('contained:throw', 'the .catch did not contain a fault')):
        if needed not in text:reasons.append(why)
    if harness['total_cost_usd'] not in (0, None) or harness['model_usage']:reasons.append('the run reported model cost or usage')
    value.update(harness=harness, verdict='PASS' if not reasons else 'FAIL', reasons=reasons,
                 scope='Headless contract only: zero-API shadow stepping, replay tool, fail-closed .catch, unserved model. Interactive rendering is not covered.')
    return value


if __name__ == '__main__':
    print(json.dumps(check(Path(os.environ.get('CLAUDE_BIN', Path.home()/'.local/bin/claude'))), indent=2))
