"""Zero-API: a worker's conversation in the NativePeers pane. The scripted parent starts one np:codex agent over the
replay bridge. Once Claude Code has dropped the finished agent (about 30 s after it ends), /np open 1 shows the
conversation in the pane, and a message typed in its field reaches the worker, which answers in the same
conversation. Expect the worker's tool step (✓ and its command) and closing mark, the person's message and the
worker's follow-up answer on screen, no toast about the send, and the replay bridge to receive the message once, as a person's follow-up. Sessions start in auto mode, so the
driver stands in for the classifier's approval of the scripted Agent call, and the classifier's own requests are
refused by the tripwire; a request naming a shadow's model must never occur. Usage: run_pane_conversation.py [MOD_FOLDER]"""
import json, os, re, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__));sys.path.insert(0, HERE)
from pty_session import Session
MOD = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, '..', '..', '..', 'integrations', 'claude', 'mod', 'np'))
log = os.path.join(tempfile.mkdtemp(prefix='np-pane-'), 'replay.ndjson')
opts = {'npeerPath': os.path.join(HERE, 'bin', 'npeer-replay')}
s = Session([MOD, os.path.join(HERE, 'driver')], label='pane-conversation', keep=os.environ.get('NP_PROBE_KEEP'), cols=170, rows=55,
            settings={'pluginConfigs': {'np': {'options': opts}, 'np@inline': {'options': opts}}},
            env={'NP_DRIVER_ALLOW_AGENT': '1', 'NP_REPLAY_SCENARIO': os.path.join(HERE, 'scenarios', 'codex_basic.json'), 'NP_REPLAY_LOG': log,
                 'NP_DRIVER_SCRIPT': os.path.join(HERE, 'scripts', 'm3_pathA.json')})
try:
    s.wait_for(r'bridge|❯', timeout=40);time.sleep(4)
    s.type_line('go')
    s.wait_for(r'· completed', timeout=60)
    # Claude Code drops a finished agent about 30 s after it ends; /np open then shows the conversation in the pane.
    time.sleep(35)
    s.type_line('/np open 1');time.sleep(5)
    opened = 'in the NativePeers pane' in s.text()
    s.type_line('Also count them');time.sleep(8)
    screen = s.text()
    replay = [json.loads(line) for line in open(log)] if os.path.exists(log) else []
    inputs = [r for r in replay if r.get('kind') == 'input']
    result = {
        'opened_in_pane': opened,
        'tool_row': bool(re.search(r'✓ pytest -q', screen)),
        'closing_note': '✓ Completed' in screen,
        'no_send_toast': not re.search(r'Sent to Codex|picking the conversation back up', screen),
        'input_reached_worker_once': len(inputs) == 1 and (inputs[0].get('body') or {}).get('origin') == 'human-claude-view',
        # The docked pane sits behind a │ border.
        'you_row': bool(re.search(r'(^|[\s│])You\s*$', screen, re.M)) and 'Also count them' in screen,
        'follow_up_answer': 'follow-up done' in screen,
        'shadow_model_requests': len([h for h in s.model_hits() if str(h.get('model', '')).startswith('np-mirror')]),
    }
    result['pass'] = all(v for k, v in result.items() if k != 'shadow_model_requests') and result['shadow_model_requests'] == 0
    print(json.dumps(result))
    print('\n'.join(line.split('│', 1)[1].rstrip() for line in screen.splitlines() if '│' in line and line.split('│', 1)[1].strip()))
    if not result['pass']:print('\n'.join(line for line in screen.splitlines() if line.strip() and '────' not in line)[-3000:])
finally:
    s.close()
