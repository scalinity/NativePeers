"""Zero-API: /np doctor typed while the session's first bridge is still starting, as the desktop app's first message
does. The fake bridge waits 3 s before it starts; the replay log counts bridge starts. Expect one start and a doctor
that reports `bridge: ready`. Usage: run_startup_race.py [MOD_FOLDER]"""
import json, os, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__));sys.path.insert(0, HERE)
from pty_session import Session
MOD = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, '..', '..', '..', 'integrations', 'claude', 'mod', 'np'))
work = tempfile.mkdtemp(prefix='np-race-');log = os.path.join(work, 'replay.ndjson');npeer = os.path.join(work, 'npeer')
with open(npeer, 'w') as f:f.write(f'#!/bin/sh\nsleep 3\nexec {HERE}/bin/npeer-replay "$@"\n')
os.chmod(npeer, 0o755)
opts = {'npeerPath': npeer}
s = Session([MOD], label='startup-race', settings={'pluginConfigs': {'np': {'options': opts}, 'np@inline': {'options': opts}}},
            env={'NP_REPLAY_SCENARIO': os.path.join(HERE, 'scenarios', 'codex_basic.json'), 'NP_REPLAY_LOG': log})
try:
    s.wait_for(r'bridge starting', timeout=40);s.type_line('/np doctor');time.sleep(12)
    starts = sum(1 for line in open(log) if json.loads(line).get('kind') == 'start') if os.path.exists(log) else 0
    doctor = [line.strip() for line in s.text().splitlines() if line.strip().startswith(('bridge:', '⎿  np: bridge'))]
    print(json.dumps({'starts': starts, 'doctor': doctor[-1:] , 'tripwire_hits': len(s.hits()), 'pass': starts == 1 and any('bridge: ready' in d for d in doctor)}))
finally:
    s.close()
