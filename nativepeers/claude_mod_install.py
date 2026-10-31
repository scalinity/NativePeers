"""Install the Claude Code `np` mod at user scope through Claude Code's own plugin commands.

The marketplace is this checkout's `integrations/claude/mod` folder. Claude Code
copies a plugin at install, and `claude plugin update` keys on the version alone,
so an install whose copy differs from the checkout is refreshed by uninstalling
and installing again. Nothing edits Claude settings directly; the native commands
make every change, and each install records what it did in an uninstall manifest.
A repeat install with an unchanged checkout performs zero operations.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import time

from .errors import PeerError, require
from .files import atomic_json, private_directory
from .profiles import ROOT

MARKETPLACE = 'nativepeers-local'
PLUGIN = 'np'
SOURCE = ROOT/'integrations/claude/mod'
CLAUDE = Path.home()/'.local/bin/claude'
TOUCHED = ['.claude/settings.json', '.claude/plugins/known_marketplaces.json', '.claude/plugins/installed_plugins.json']


def _claude(args, timeout=120, executable=None):
    result = subprocess.run([str(executable or CLAUDE), *args], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    return result.returncode, result.stdout, result.stderr


def _json(args, executable=None):
    code, out, err = _claude([*args, '--json'], executable=executable)
    require(code == 0, 'CAPABILITY_MISMATCH', f'claude {" ".join(args)} failed: {err.strip()[:300]}')
    try:return json.loads(out)
    except ValueError:return json.loads(out.strip().splitlines()[-1])


def inspect(executable=None, source=SOURCE):
    """Where the mod stands: marketplace, installation and enablement."""
    markets = _json(['plugin', 'marketplace', 'list'], executable)
    market = next((m for m in markets if m.get('name') == MARKETPLACE), None)
    plugins = _json(['plugin', 'list'], executable)
    plugin = next((p for p in plugins if p.get('id') == f'{PLUGIN}@{MARKETPLACE}'), None)
    return dict(marketplace=market, plugin=plugin, source=str(source),
                marketplace_matches=bool(market) and Path(market.get('path') or market.get('installLocation') or '').resolve() == Path(source).resolve())


def stale(plugin, source):
    """The installed copy differs from the checkout's plugin folder; the engine's own type files are not compared."""
    if not plugin.get('installPath'):
        return False
    installed = Path(plugin['installPath'])
    if not installed.is_dir():
        return True  # A recorded installation whose files are gone is reinstalled.
    def tree(root):
        return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file() and p.relative_to(root).parts[:2] != ('.claude-plugin', 'types')}
    return tree(installed) != tree(Path(source)/PLUGIN)


def plan(executable=None, source=SOURCE, remove=False):
    state = inspect(executable, source)
    market, plugin = state['marketplace'], state['plugin']
    operations = []
    if remove:
        if plugin:operations.append(['plugin', 'uninstall', f'{PLUGIN}@{MARKETPLACE}', '--scope', 'user'])
        if market and state['marketplace_matches']:operations.append(['plugin', 'marketplace', 'remove', MARKETPLACE])
        return state, operations
    if market and not state['marketplace_matches']:
        raise PeerError('OWNERSHIP_CONFLICT', f'Marketplace {MARKETPLACE} already points elsewhere',
                        details={'configured': market.get('path') or market.get('installLocation'), 'expected': str(source)})
    if not market:operations.append(['plugin', 'marketplace', 'add', str(source)])
    if plugin and stale(plugin, source):
        operations += [['plugin', 'uninstall', f'{PLUGIN}@{MARKETPLACE}', '--scope', 'user'], ['plugin', 'install', f'{PLUGIN}@{MARKETPLACE}', '--scope', 'user']]
    elif not plugin:operations.append(['plugin', 'install', f'{PLUGIN}@{MARKETPLACE}', '--scope', 'user'])
    elif not plugin.get('enabled'):operations.append(['plugin', 'enable', f'{PLUGIN}@{MARKETPLACE}', '--scope', 'user'])
    return state, operations


def _backup(home, root):
    stamp = time.strftime('%Y%m%dT%H%M%S')
    target = root/'backups/claude-lineage'/stamp
    saved = []
    for relative in TOUCHED:
        path = home/relative
        if path.exists():
            destination = target/relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination);saved.append(str(destination))
    return saved


def apply(journal, *, dry_run=False, remove=False, executable=None, source=SOURCE, home=None):
    home = Path(home or Path.home())
    state, operations = plan(executable, source, remove=remove)
    value = dict(provider='claude', component='np-mod', marketplace=MARKETPLACE, source=str(source), dry_run=dry_run, remove=remove,
                 operations=[' '.join(['claude', *op]) for op in operations], state=state)
    if dry_run or not operations:return value
    backups = _backup(home, ROOT)
    done = []
    for op in operations:
        code, out, err = _claude(op, executable=executable)
        if code != 0:
            raise PeerError('INSTALL_FAILED', f'claude {" ".join(op)} failed', details={'stderr': err.strip()[:600], 'completed': done, 'backups': backups})
        done.append(' '.join(['claude', *op]))
    manifest = journal.root/'install/claude-mod.json'
    private_directory(manifest.parent)
    record = dict(schema='nativepeers.claude-mod-install.v1', at=time.time(), operations=done, backups=backups, source=str(source),
                  uninstall=['claude plugin uninstall np@nativepeers-local --scope user', 'claude plugin marketplace remove nativepeers-local'], removed=remove)
    atomic_json(manifest, record)
    value.update(applied=done, backups=backups, manifest=str(manifest), state=inspect(executable, source))
    return value
