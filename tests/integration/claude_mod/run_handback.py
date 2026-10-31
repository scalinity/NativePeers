"""Zero-API: an np shadow in a session in auto mode, where Claude Code requires every subagent run to end with a
SubagentHandback call (the desktop app's Auto). The scripted parent starts one np:codex agent over the replay bridge.
Expect the engine's handback note in the shadow's transcript, one SubagentHandback call carrying the worker's final
text and ids, no enforcement row, and the report reaching the parent as its task notification without anyone stopping
the run. Auto mode's permission classifier asks the tripwire for a model and is refused; a request in a shadow's own
loop (an np-mirror model) must never occur; the driver's tool.check stands in for the classifier's approval of the
scripted call. With --refuse every SubagentHandback is refused, as a classifier refusal would be: expect three tries,
then the shadow's note, and the run ending by itself with the parent notified.
Usage: run_handback.py [MOD_FOLDER] [--refuse]"""
import glob, json, os, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__));sys.path.insert(0, HERE)
from pty_session import Session
args = [a for a in sys.argv[1:] if a != '--refuse'];REFUSE = '--refuse' in sys.argv[1:]
MOD = os.path.abspath(args[0] if args else os.path.join(HERE, '..', '..', '..', 'integrations', 'claude', 'mod', 'np'))
log = os.path.join(tempfile.mkdtemp(prefix='np-handback-'), 'replay.ndjson')
opts = {'npeerPath': os.path.join(HERE, 'bin', 'npeer-replay')}
s = Session([MOD, os.path.join(HERE, 'driver')], label='handback', args=['--permission-mode', 'auto'], keep=os.environ.get('NP_PROBE_KEEP'),
            settings={'pluginConfigs': {'np': {'options': opts}, 'np@inline': {'options': opts}}},
            # The driver's NP_DRIVER_ALLOW_AGENT stands in for the classifier's approval (the tripwire refuses its model).
            env={'NP_DRIVER_ALLOW_AGENT': '1', **({'NP_DRIVER_DENY_HANDBACK': '1'} if REFUSE else {}), 'NP_REPLAY_SCENARIO': os.path.join(HERE, 'scenarios', 'codex_basic.json'), 'NP_REPLAY_LOG': log,
                 'NP_DRIVER_SCRIPT': os.path.join(HERE, 'scripts', 'm3_pathA.json')})


def rows(pattern):
    out = []
    for path in glob.glob(str(s.home / '.claude' / 'projects' / '*' / pattern)):
        for line in open(path):
            try:out.append(json.loads(line))
            except ValueError:pass
    return out


def texts(row):
    content = (row.get('message') or {}).get('content')
    if isinstance(content, str):return [content]
    return [b.get('text', '') if b.get('type') == 'text' else json.dumps(b.get('input') or b.get('content') or '') for b in content or [] if isinstance(b, dict)]


try:
    s.wait_for(r'bridge|❯', timeout=40);time.sleep(4)
    s.type_line('go');started = time.time();notified = None;approved = 0
    while time.time() - started < 90 and notified is None:
        screen = s.text()
        if 'Do you want' in screen and approved < 3:s.key('enter');approved += 1
        for row in rows('*.jsonl'):
            if row.get('type') == 'user' and any('<task-notification>' in t for t in texts(row)):notified = round(time.time() - started, 1)
        time.sleep(1)
    shadow = rows('*/subagents/agent-*.jsonl')
    calls = [b for r in shadow if r.get('type') == 'assistant' for b in (r['message'].get('content') or []) if isinstance(b, dict) and b.get('type') == 'tool_use']
    handbacks = [c for c in calls if c.get('name') == 'SubagentHandback']
    report = str((handbacks[0].get('input') or {}).get('message', '')) if handbacks else ''
    ids = {c.get('id') for c in handbacks}
    refusals = [t for r in shadow if r.get('type') == 'user' for b in (r['message'].get('content') or []) if isinstance(b, dict)
                and b.get('type') == 'tool_result' and b.get('tool_use_id') in ids for t in texts({'message': {'content': [b]}})]
    meta = [json.load(open(p)) for p in glob.glob(str(s.home / '.claude' / 'projects' / '*' / '*' / 'subagents' / '*.meta.json'))]
    result = {
        'handback_note_seen': any(r.get('type') == 'user' and any('SubagentHandback(' in t for t in texts(r)) for r in shadow),
        'handback_calls': len(handbacks),
        'report_has_final_text_and_ids': '— NativePeers · codex' in report and 'worker ' in report and 'group ' in report,
        'handback_results': [str(t)[:300] for t in refusals][:2],
        'refusal_note': any('could not hand this report' in t for r in shadow if r.get('type') == 'assistant' for t in texts(r)),
        'enforcement_rows': sum(1 for r in shadow if any('handback-send-enforce' in t for t in texts(r))),
        'notified_after_s': notified,
        'stopped_by_user': any(m.get('stoppedByUser') for m in meta),
        'permission_prompts_accepted': approved,
        'shadow_model_requests': len([h for h in s.model_hits() if str(h.get('model', '')).startswith('np-mirror')]),
        'classifier_requests_refused': len([h for h in s.model_hits() if not str(h.get('model', '')).startswith('np-mirror')]),
    }
    common = result['handback_note_seen'] and result['report_has_final_text_and_ids'] and notified is not None and not result['stopped_by_user'] and result['shadow_model_requests'] == 0
    result['pass'] = common and (result['handback_calls'] == 3 and result['refusal_note'] if REFUSE else result['handback_calls'] == 1 and result['enforcement_rows'] == 0)
    result['mode'] = 'refuse' if REFUSE else 'deliver'
    print(json.dumps(result))
    if not result['pass']:print('\n'.join(l for l in s.text().splitlines() if l.strip() and '────' not in l)[-2500:])
finally:
    s.close()
