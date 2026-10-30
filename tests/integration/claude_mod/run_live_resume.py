"""Live resume check (AC11): `claude --resume` of a finished rig session lists its workers and keeps completed shadows.

Live-test only. No model input: nothing is typed but `/np` commands, so the resumed session makes no parent-model
request and starts no worker. Usage: run_live_resume.py RIG KEEP SESSION_ID
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_session import Session

HERE = Path(__file__).resolve().parent
RIG, KEEP, SESSION_ID = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), sys.argv[3]
KEEP.mkdir(parents=True, exist_ok=True)
opts = {'npeerPath': str(RIG/'npeer-wt')}
session = Session([RIG/'mod-np'], live=True, cwd=RIG/'fixture', keep=KEEP, label='live-resume', cols=160, rows=55,
                  args=['--resume', SESSION_ID, '--permission-mode', 'default', '--model', 'sonnet',
                        '--settings', json.dumps({'pluginConfigs': {'np': {'options': opts}, 'np@inline': {'options': opts}}})],
                  env={'NPEER_HOME': str(RIG/'npeer-home')})


def snap(name, tail=3000):
    text = session.snap(name)
    print('##', name, round(time.time() - session.started, 1));print('\n'.join(l for l in text.splitlines() if l.strip() and '────' not in l)[-tail:]);sys.stdout.flush()
    return text


try:
    session.wait_for(r'⟐ np', timeout=60);time.sleep(4);snap('00-resumed', 4000)
    session.type_line('/np');time.sleep(4);snap('01-np', 3000)
    session.key('ctrl+o');time.sleep(2);snap('02-expanded', 6000);session.key('ctrl+o')
finally:
    session.close()
    print('swept', session.swept)
