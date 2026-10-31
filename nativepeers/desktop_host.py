"""Desktop-hosted Claude Code engines as Claude owners (docs/CLAUDE_LINEAGE_MOD.md §6.7).

The Claude desktop app runs its own copy of the Claude Code engine at
`~/Library/Application Support/Claude/claude-code/<version>/<build>/claude.app/Contents/MacOS/claude`. Every such copy is
a native boundary; it is a Claude owner only when its exact resolved path and SHA-256 hold an approval in
`compat/runtime.json` under `hosts.claude-desktop`. An unapproved copy is approved once, by the np bridge rather than the
short SessionStart hook, on a private snapshot of its app bundle: Anthropic's code signature (the CLI's team and
identifier) and a version in the supported series. A definite failure is recorded for that path and those bytes; a check
that could not run records nothing. Same-user processes stay out of scope (§10).
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from .compatibility import fingerprint
from .errors import PeerError, require
from .files import advisory_lock, atomic_json

KEY = 'claude-desktop'
SERIES, MINIMUM = (2, 1), (2, 1, 309)
REFUSED_SIGNATURE = (1, 3)  # codesign: verification failed, requirement unsatisfied


def base():
    return Path.home()/'Library'/'Application Support'/'Claude'/'claude-code'


def candidate(executable):
    """`<version>/<build>/claude.app/Contents/MacOS/claude` under the desktop app's own engine folder."""
    try:
        parts = Path(executable).relative_to(base()).parts
    except ValueError:
        return False
    return len(parts) == 6 and parts[2:] == ('claude.app', 'Contents', 'MacOS', 'claude')


def records(journal):
    """Records keyed by exact resolved executable path; each carries the SHA-256 it was checked with."""
    try:
        value = json.loads((Path(journal.root)/'compat/runtime.json').read_text()).get('hosts', {}).get(KEY, {})
    except (OSError, ValueError, AttributeError):
        return {}
    return value if isinstance(value, dict) else {}


def paths(journal):
    """Approved engine paths whose file keeps the approved size: frequent hook events and caller binding match these by path."""
    out = set()
    for path, record in records(journal).items():
        try:
            if not record.get('refused') and Path(path).stat().st_size == record['size']:
                out.add(path)
        except (OSError, KeyError, TypeError, AttributeError):
            continue
    return out


def approved(journal, executable):
    """The exact bytes at this path hold an approval (registration checks the hash, not only the size)."""
    record = records(journal).get(str(executable))
    if not record or record.get('refused'):
        return False
    try:
        return fingerprint(executable, fresh=True)['sha256'] == record.get('sha256')
    except OSError:
        return False


def approve(journal, executable):
    """Check and record an unapproved engine once; None when it is refused or the check could not run."""
    if not candidate(executable):
        return None
    try:
        runtime = fingerprint(executable, fresh=True)
    except OSError:
        return None
    known = records(journal).get(runtime['path'])
    if known and known.get('sha256') == runtime['sha256']:
        return None if known.get('refused') else known
    return check(journal, runtime)


@contextmanager
def bundle_snapshot(journal, runtime):
    """The checks consume one private copy of the app bundle: its signature seals Info.plist beside the executable."""
    app = Path(runtime['path']).parents[2]
    with tempfile.TemporaryDirectory(prefix='desktop-check-', dir=Path(journal.root)/'compat') as directory:
        copy = Path(directory)/'claude.app'
        shutil.copytree(app, copy, symlinks=True)
        executable = copy/'Contents/MacOS/claude'
        require(fingerprint(executable, fresh=True)['sha256'] == runtime['sha256'], 'CAPABILITY_MISMATCH',
                'Desktop engine changed while copying', details={'compatibility_state': 'UPDATE_RACED'})
        yield copy, executable


def check(journal, runtime):
    from .runtime_updates import CONTRACTS, READ_ONLY_PROFILE, version
    if sys.platform != 'darwin':
        return None
    contract = CONTRACTS['claude']
    requirement = f'anchor apple generic and certificate leaf[subject.OU] = "{contract["team"]}" and identifier "{contract["identifier"]}"'
    try:
        with bundle_snapshot(journal, runtime) as (bundle, executable):
            # Fixed metadata commands only: no prompt, no stdin, no network and no writes.
            signed = subprocess.run(['/usr/bin/codesign', '--verify', '--strict', '-R=' + requirement, str(bundle)],
                                    stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
            shown = subprocess.run(['/usr/bin/sandbox-exec', '-p', READ_ONLY_PROFILE, str(executable), '--version'],
                                   stdin=subprocess.DEVNULL, capture_output=True, timeout=5)
        require(fingerprint(runtime['path'], fresh=True) == runtime, 'CAPABILITY_MISMATCH', 'Desktop engine changed during the check',
                details={'compatibility_state': 'UPDATE_RACED'})
    except (OSError, subprocess.TimeoutExpired, PeerError):
        return None
    observed = (shown.stdout + shown.stderr).decode('utf-8', errors='replace').strip()[:120]
    numbers = version(observed) if shown.returncode == 0 else None
    if signed.returncode in REFUSED_SIGNATURE:
        refused = 'The desktop engine does not satisfy the vendor code-signature requirement'
    elif signed.returncode or numbers is None:
        return None  # An unsuccessful invocation says nothing about the engine: nothing is recorded and a later start retries.
    elif numbers[:2] != SERIES or numbers < MINIMUM:
        refused = 'The desktop engine version is outside the supported series'
    else:
        refused = None
    record = dict(runtime, version=observed, at=time.time(), **({'refused': refused} if refused else {'checks': ['vendor-signature', 'supported-version']}))
    receipt = Path(journal.root)/'compat/runtime.json'
    with advisory_lock(Path(journal.root)/'locks/runtime.lock'):
        document = json.loads(receipt.read_text()) if receipt.exists() else {'schema_version': 1, 'providers': {}}
        document.setdefault('hosts', {}).setdefault(KEY, {})[runtime['path']] = record
        atomic_json(receipt, document)
    return None if refused else record
