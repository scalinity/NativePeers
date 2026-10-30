"""Post-install production check (AC1/AC2): one real parent turn fans out to all four providers through the installed
np plugin and the production NativePeers state.

Live-test only, under the durable budget, and only after `npeer install --claude-mod`. The disposable session runs in
a fixture repository; npguard (loaded with --plugin-dir) lets the first main turn reach the model and answers every
later one locally, so the four notifications spend no parent turn.
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_session import Session

HERE = Path(__file__).resolve().parent
FIXTURE = Path(sys.argv[1]).resolve()
KEEP = Path(sys.argv[2]).resolve()
KEEP.mkdir(parents=True, exist_ok=True)
FANOUT = ('Spin up four NativePeers delegates at once, one Agent call each, all in the same response:\n'
          '- np:codex with the header ```np {"model":"gpt-6.1-sol","effort":"low","title":"Codex review"}```\n'
          '- np:grok with the header ```np {"model":"grok-4.7","title":"Grok review"}```\n'
          '- np:antigravity with the header ```np {"model":"gemini-4-argon-high","title":"Gemini review"}```\n'
          '- np:claude with the header ```np {"model":"sonnet","title":"Claude review"}```\n'
          'Start each prompt with its header, then this brief: "Read tally/count.py in the current repository and report '
          'one easy win in one sentence. Do not edit any file." End your turn after launching them; do not wait.')

session = Session([HERE/'mods/turn_guard'], live=True, cwd=FIXTURE, keep=KEEP, label='live-production', cols=160, rows=55,
                  args=['--permission-mode', 'default', '--model', 'sonnet'],
                  env={'NP_GUARD_REAL_TURNS': '1', 'NP_GUARD_LOG': str(KEEP/'guard.ndjson')})
marks = {}


def snap(name, tail=3000):
    text = session.snap(name)
    print('##', name, round(time.time() - session.started, 1));print('\n'.join(l for l in text.splitlines() if l.strip() and '────' not in l)[-tail:]);sys.stdout.flush()
    return text


try:
    for _ in range(40):
        text = session.text()
        if 'Yes, I trust this folder' in text:
            session.key('down');time.sleep(0.5)
            if '❯ Yes, I trust this folder' in session.text():session.key('enter')
            time.sleep(3);continue
        if '❯' in text and '⟐ np' in text:break
        time.sleep(1)
    ready = snap('00-ready', 1200)
    if '⟐ np' not in ready or 'bridge down' in ready or 'bridge starting' in ready:
        raise SystemExit('np bridge not ready; nothing was submitted')
    session.type_line('/np doctor');time.sleep(4);snap('01-doctor', 2500)
    session.type_line(FANOUT);marks['fanout'] = time.time()
    session.wait_for(r'4 delegates', timeout=150);marks['rows'] = time.time();time.sleep(4);snap('02-rows')
    session.wait_for(r'4 delegates · 0 running', timeout=480);marks['done'] = time.time();time.sleep(6);snap('03-done', 4000)
    session.type_line('/np');time.sleep(3);snap('04-np', 2500)
    session.type_line('/cost');time.sleep(4);snap('05-cost', 2500)
finally:
    marks['closed'] = time.time();(KEEP/'marks.json').write_text(json.dumps(marks))
    session.close()
    print('swept', session.swept)
