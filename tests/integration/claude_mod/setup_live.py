"""Prepare an isolated live-test rig: state home, worktree launcher and fixture repository.

The state home is separate from the production NativePeers journal, so the
branch's journal migrations never touch production state before merge. It holds
a read-only copy of the production runtime approvals (the same executables,
pinned by the same hashes). Nothing here submits model input.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
WORKTREE = HERE.parents[2]
PYTHON = Path('/Users/danny/Documents/Tools/NativePeers/.venv/bin/python')
PRODUCTION = Path.home()/'.local/state/nativepeers'

FILES = {
    'README.md': '# tally\n\nA tiny word-count tool used as a NativePeers live-test fixture.\n',
    'tally/__init__.py': '',
    'tally/count.py': (
        'import re\n\n\n'
        'def words(text):\n    # TODO: handle apostrophes\n    return re.findall(r"[a-z]+", text.lower())\n\n\n'
        'def count(text):\n    result = {}\n    for word in words(text):\n        if word in result:\n            result[word] = result[word] + 1\n'
        '        else:\n            result[word] = 1\n    return result\n\n\n'
        'def count_words(text):\n    # Duplicate of count(); kept by accident.\n    result = {}\n    for word in words(text):\n'
        '        result[word] = result.get(word, 0) + 1\n    return result\n'),
    'tally/cli.py': (
        'import sys\n\nfrom .count import count\n\n\n'
        'def main():\n    text = open(sys.argv[1]).read()\n    for word, n in sorted(count(text).items(), key=lambda kv: -kv[1])[:10]:\n'
        '        print(word, n)\n'),
    'tests/test_count.py': 'from tally.count import count\n\n\ndef test_count():\n    assert count("a a b") == {"a": 2, "b": 1}\n',
}


def setup(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    home = root/'npeer-home'
    home.mkdir(mode=0o700, exist_ok=True);os.chmod(home, 0o700)
    (home/'compat').mkdir(mode=0o700, exist_ok=True)
    shutil.copy2(PRODUCTION/'compat/runtime.json', home/'compat/runtime.json');os.chmod(home/'compat/runtime.json', 0o600)
    launcher = root/'npeer-wt'
    launcher.write_text(f'#!/bin/sh\nexport NPEER_HOME={home}\nexport PYTHONPATH={WORKTREE}\nexec {PYTHON} -m nativepeers "$@"\n')
    launcher.chmod(0o755)
    repo = root/'fixture'
    if not (repo/'.git').exists():
        for name, text in FILES.items():
            path = repo/name;path.parent.mkdir(parents=True, exist_ok=True);path.write_text(text)
        subprocess.run(['git', 'init', '-q', '-b', 'main', str(repo)], check=True)
        subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(repo), 'commit', '-q', '-m', 'Fixture'], check=True)
    return dict(root=str(root), npeer_home=str(home), launcher=str(launcher), fixture=str(repo))


if __name__ == '__main__':
    print(json.dumps(setup(Path(sys.argv[1]).resolve()), indent=1))
