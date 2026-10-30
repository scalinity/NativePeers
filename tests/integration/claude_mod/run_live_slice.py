"""Live thin slice (M3): one real parent turn spawns one real Codex worker through Agent(np:codex).

Live-test only, under the durable budget. The disposable session runs in the fixture repository with an
isolated NPEER_HOME; npguard answers every later main turn locally, so the completion notification is
observed without spending a parent turn.
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_session import Session

HERE = Path(__file__).resolve().parent
MOD = HERE.parents[2]/'integrations/claude/mod/np'
RIG = Path(sys.argv[1]).resolve()
KEEP = Path(sys.argv[2]).resolve()
PROMPT = ('Use exactly one np:codex agent. Start its prompt with this header on its own lines:\n'
          '```np\n{"model":"gpt-6.1-sol","effort":"low","title":"Live slice"}\n```\n'
          'then this brief: "Read tally/count.py in the current repository and report one easy win in one sentence. Do not edit any file." '
          'After launching it, end your turn without waiting.')

opts = {'npeerPath': str(RIG/'npeer-wt')}
session = Session([MOD, HERE/'mods/turn_guard'], live=True, cwd=RIG/'fixture', keep=KEEP, label='live-slice', cols=150, rows=50,
                  args=['--permission-mode', 'default', '--model', 'sonnet', '--settings', json.dumps({'pluginConfigs': {'np': {'options': opts}, 'np@inline': {'options': opts}}})],
                  env={'NPEER_HOME': str(RIG/'npeer-home'), 'NP_GUARD_REAL_TURNS': '1', 'NP_GUARD_LOG': str(KEEP/'guard.ndjson')})
marks = {}


def snap(name, tail=2600):
    text = session.snap(name)
    print('##', name, round(time.time() - session.started, 1));print('\n'.join(l for l in text.splitlines() if l.strip() and '────' not in l)[-tail:]);sys.stdout.flush()
    return text


try:
    for _ in range(40):
        text = session.text()
        if 'Yes, I trust this folder' in text:
            # The dialog's default is 'No, exit': move to the trust option first.
            session.key('down');time.sleep(0.5)
            if '❯ Yes, I trust this folder' in session.text():session.key('enter')
            time.sleep(3);continue
        if '❯' in text and '⟐ np' in text:break
        time.sleep(1)
    ready = snap('00-ready', 1200)
    if '⟐ np' not in ready or 'bridge ready' in ready and False:
        raise SystemExit('np band not drawn: the bridge is not ready; nothing was submitted')
    if 'bridge down' in ready or 'bridge starting' in ready:
        raise SystemExit('np bridge not ready; nothing was submitted')
    session.type_line(PROMPT);marks['prompt'] = time.time()
    session.wait_for(r'np:codex', timeout=120);marks['row'] = time.time();snap('01-row')
    time.sleep(4);session.key('down');session.key('down');session.key('enter');snap('02-view')
    session.wait_for(r'— NativePeers', timeout=420);marks['final'] = time.time();snap('03-final', 4000)
    time.sleep(8);snap('04-after-notification')
    session.key('down');session.key('up');session.key('enter');time.sleep(2)
    session.type_line('/cost');time.sleep(3);snap('05-cost', 1500)
finally:
    marks['closed'] = time.time()
    (KEEP/'marks.json').write_text(json.dumps(marks))
    session.close()
    print('swept', session.swept)
