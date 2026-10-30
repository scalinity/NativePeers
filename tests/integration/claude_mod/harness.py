"""Zero-API headless harness for the np mod and the M0 gates.

Every run starts the tripwire, clears the environment (`env -i` semantics) with
a temporary HOME, points ANTHROPIC_BASE_URL at the tripwire, supplies a dummy
API key (so a fallthrough leaves the process and lands on the tripwire rather
than failing before any request; it also outranks any OAuth source) and
reports the zero-API assertions:
duration_api_ms == 0, total_cost_usd == 0, empty modelUsage, zero tripwire
hits and no API error rows. The tripwire count is authoritative: gate G0
observed duration_api_ms == 0 on a run whose six refused requests all reached
the tripwire. A run that violates one is reported, never silently passed.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from tripwire import Tripwire

CLAUDE = Path(os.environ.get('NP_CLAUDE_BIN', '/Users/danny/.local/bin/claude'))
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
DUMMY_KEY = 'sk-ant-api03-nativepeers-tripwire-dummy-0000000000'


def run(prompt, plugin_dirs, *, env=None, args=(), timeout=180, home=None, cwd=None, expect_hits=False, label='run', keep=None, prefix=()):
    plugin_dirs = [Path(p).resolve() for p in plugin_dirs]
    scratch = Path(tempfile.mkdtemp(prefix='npharness-'))
    home = Path(home) if home else scratch/'home'
    home.mkdir(parents=True, exist_ok=True)
    work = Path(cwd) if cwd else scratch/'work'
    work.mkdir(parents=True, exist_ok=True)
    debug = scratch/'debug.log'
    with Tripwire(scratch/'tripwire.ndjson') as wire:
        environment = {'HOME': str(home), 'PATH': f'{CLAUDE.parent}:/usr/bin:/bin:/usr/sbin:/sbin',
                       'ANTHROPIC_BASE_URL': wire.url, 'TMPDIR': str(scratch/'tmp'), 'LANG': 'en_US.UTF-8',
                       'ANTHROPIC_API_KEY': DUMMY_KEY, 'NP_MOD_HEADLESS': '1', 'DISABLE_AUTOUPDATER': '1', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1'}
        (scratch/'tmp').mkdir(exist_ok=True)
        environment.update(env or {})
        argv = [*prefix, str(CLAUDE), '-p', prompt, '--output-format', 'stream-json', '--verbose', '--debug-file', str(debug),
                *[x for p in plugin_dirs for x in ('--plugin-dir', str(p))], *args]
        started = time.time()
        try:
            proc = subprocess.run(argv, env=environment, cwd=work, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
            code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            code, stdout, stderr = None, exc.stdout or '', exc.stderr or ''
            stdout = stdout.decode() if isinstance(stdout, bytes) else stdout
            stderr = stderr.decode() if isinstance(stderr, bytes) else stderr
        elapsed = time.time() - started
        hits = list(wire.hits)
    events = []
    for line in stdout.splitlines():
        try:events.append(json.loads(line))
        except json.JSONDecodeError:pass
    result = next((e for e in reversed(events) if e.get('type') == 'result'), None)
    api_errors = [e for e in events if 'API Error' in json.dumps(e)]
    checks = {
        'result_record': result is not None,
        'duration_api_ms_zero': bool(result) and result.get('duration_api_ms') == 0,
        'total_cost_usd_zero': bool(result) and result.get('total_cost_usd') == 0,
        'model_usage_empty': bool(result) and not result.get('modelUsage'),
        'tripwire_hits_zero': len(hits) == 0,
        'no_api_error_rows': not api_errors,
    }
    record = {'label': label, 'exit_code': code, 'elapsed_s': round(elapsed, 2), 'tripwire_hits': len(hits),
              'tripwire_paths': sorted({h['path'] for h in hits}), 'checks': checks,
              'zero_api': all(checks.values()), 'result': {k: result.get(k) for k in
              ('subtype', 'is_error', 'duration_api_ms', 'total_cost_usd', 'modelUsage', 'num_turns', 'result')} if result else None,
              'scratch': str(scratch), 'expect_hits': expect_hits}
    (scratch/'stream.ndjson').write_text(stdout)
    (scratch/'stderr.txt').write_text(stderr)
    if keep:
        keep = Path(keep);keep.mkdir(parents=True, exist_ok=True)
        for name in ('stream.ndjson', 'stderr.txt', 'debug.log', 'tripwire.ndjson'):
            if (scratch/name).exists():(keep/name).write_bytes((scratch/name).read_bytes())
        (keep/'record.json').write_text(json.dumps(record, indent=2))
    record['events'] = events
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prompt', default='go')
    parser.add_argument('--plugin-dir', action='append', default=[])
    parser.add_argument('--keep')
    parser.add_argument('--label', default='run')
    parser.add_argument('--expect-hits', action='store_true')
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('extra', nargs='*')
    args = parser.parse_args()
    record = run(args.prompt, args.plugin_dir, args=args.extra, timeout=args.timeout, keep=args.keep,
                 label=args.label, expect_hits=args.expect_hits)
    record.pop('events')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
