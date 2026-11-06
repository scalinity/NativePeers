"""Exercise report ownership in the documented headless replay harness.

Auto classifier calls reach the refusing tripwire and are counted separately.
No shadow model request is allowed. Folded-native deliberately scripts mixed
owners to exercise that branch; it does not claim a natural lease transition.
Real native push and lease races remain covered by test_mirror_routes.
"""
import argparse
import json
from pathlib import Path
import tempfile

from harness import HERE, REPO, run

CASES = ['adopted', 'direct', 'off', 'before', 'after', 'queued', 'folded-native', 'folded-native-plain', 'folded-repeat', 'folded-missing', 'reattach', 'native-plain']


def message_blocks(event):
    message = event.get('message')
    content = message.get('content') if isinstance(message, dict) else None
    return [block for block in content if isinstance(block, dict)] if isinstance(content, list) else []


def reattach(keep):
    # command.run queues behind a running turn. Exercise the real slash command
    # at the interactive prompt, using the repository's existing PTY harness.
    import glob
    import time
    from pty_session import Session
    with tempfile.TemporaryDirectory(prefix='np-reattach-') as directory:
        directory = Path(directory)
        if keep:keep.mkdir(parents=True, exist_ok=True)
        log = (keep or directory) / 'replay.ndjson'
        script = directory / 'driver.json'
        script.write_text(json.dumps({'notifications': 'ack', 'steps': [{'tools': [{'name': 'Agent', 'input': {'subagent_type': 'np:codex', 'description': 'Reattach fixture', 'prompt': 'Return the scripted fixture report.'}}]}, {'text': 'main fixture complete'}]}))
        opts = {'npeerPath': str(HERE / 'bin/npeer-replay')}
        session = Session([HERE / 'driver', REPO / 'integrations/claude/mod/np'], label='delivery-reattach', keep=keep,
                          args=['--permission-mode', 'auto'], settings={'pluginConfigs': {'np': {'options': opts}, 'np@inline': {'options': opts}}},
                          env={'NP_DRIVER_SCRIPT': str(script), 'NP_DRIVER_ALLOW_AGENT': '1', 'NP_REPLAY_LOG': str(log), 'NP_REPLAY_SCENARIO': str(HERE / 'scenarios/codex_basic.json')})
        def rows():return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        try:
            ready = session.wait_for('❯', timeout=40)
            session.type_line('go')
            deadline = time.monotonic()+40
            while time.monotonic()<deadline and not any(r['kind']=='report-route' and r['route']=='MIRROR_DELIVERED' for r in rows()):time.sleep(0.1)
            initial = any(r['kind']=='report-route' and r['route']=='MIRROR_DELIVERED' for r in rows())
            session.type_line('/np attach 1')
            deadline = time.monotonic()+40
            while time.monotonic()<deadline and not any(r['kind']=='report-route' and r['route']=='MIRROR_DELIVERED' and not r['changed'] for r in rows()):time.sleep(0.1)
            reattached = any(r['kind']=='report-route' and r['route']=='MIRROR_DELIVERED' and not r['changed'] for r in rows())
            time.sleep(1)
            handbacks = []
            for path in glob.glob(str(session.home / '.claude/projects/*/*/subagents/agent-*.jsonl')):
                for line in Path(path).read_text().splitlines():
                    try:row=json.loads(line)
                    except ValueError:continue
                    handbacks.extend(b for b in message_blocks(row) if b.get('type')=='tool_use' and b.get('name')=='SubagentHandback')
            hits = session.model_hits()
            shadow_requests = sum(str(h.get('model','')).startswith('np-mirror') for h in hits)
            checks = dict(prompt_ready=ready, initial_delivered=initial, reattached_after_delivery=reattached, one_report=len(handbacks)==1, no_shadow_model_request=shadow_requests==0)
            result = dict(case='reattach', command='/np attach 1', checks=checks, passed=all(checks.values()), handbacks=len(handbacks), shadow_model_requests=shadow_requests, classifier_requests_refused=len(hits)-shadow_requests)
        finally:session.close()
        result['processes_found_at_close'] = session.swept
        print(json.dumps(result, indent=2))
        if not result['passed']:raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=CASES, required=True)
    parser.add_argument('--keep', type=Path)
    args = parser.parse_args();case = args.case
    if case == 'reattach':return reattach(args.keep)
    adopted = case in ('adopted', 'queued', 'native-plain')
    direct = not adopted and case != 'off'
    queued = case == 'queued' or case.startswith('folded-')
    plain = case.endswith('-plain')
    with tempfile.TemporaryDirectory(prefix='np-delivery-') as directory:
        directory = Path(directory)
        tools = [{'name': 'Agent', 'input': {'subagent_type': 'np:codex', 'description': 'Ownership fixture', 'prompt': 'Return the scripted fixture report.'}}] if direct else [
            {'name': 'Bash', 'input': {'command': 'echo npeer spawn --spec fixture.json --json', 'description': 'Replay adoption trigger'}}]
        steps = [{'tools': tools}]
        if queued:steps.append({'sleep_ms': 500, 'tools': [{'name': 'SendMessage', 'input': {'to': '${agent:np:' + ('grok' if adopted else 'codex') + '}', 'message': 'Run the next scripted phase.'}}]})
        steps.append({'sleep_ms': 8000, 'text': 'main fixture complete'})
        script = directory / 'driver.json';script.write_text(json.dumps({'notifications': 'ack', 'steps': steps}))
        scenario = json.loads((HERE / 'scenarios' / ('codex_basic.json' if direct else 'adopt_grok.json')).read_text())
        if case in ('before', 'after'):scenario['surfacing_failure'] = dict(generation=1, when=case)
        if queued:
            worker = scenario['workers']['codex'] if direct else scenario['external']
            scenario['delay_ms'] = 300
            worker['followup'] = [{'kind': 'message.completed', 'data': {'text': 'FOLD_SECOND', 'is_final_candidate': True}}, {'kind': 'task.terminal', 'data': {'status': 'completed', 'final_text': 'FOLD_SECOND'}}]
            if case.startswith('folded-native'):scenario['input_owner'] = 'native'
            if case == 'folded-repeat':worker['initial_route'] = 'MIRROR_SURFACING'
            if case == 'folded-missing':scenario['surfacing_failure'] = dict(generation=1, when='before', count=4)
        scenario_path = directory / 'scenario.json';scenario_path.write_text(json.dumps(scenario))
        if args.keep:args.keep.mkdir(parents=True, exist_ok=True)
        log = (args.keep or directory) / 'replay.ndjson'
        opts = {'npeerPath': str(HERE / 'bin/npeer-replay'), 'adoption': 'off' if case == 'off' else 'auto'}
        settings = {'permissions': {'allow': ['Bash(echo:*)']}, 'pluginConfigs': {'np': {'options': opts}, 'np@inline': {'options': opts}}}
        record = run('go', [HERE / 'driver', REPO / 'integrations/claude/mod/np'],
                     env={'NP_DRIVER_SCRIPT': str(script), 'NP_DRIVER_ALLOW_AGENT': '1', 'NP_DRIVER_ALLOW_MESSAGE': '1', 'NP_REPLAY_LOG': str(log), 'NP_REPLAY_SCENARIO': str(scenario_path)},
                     args=['--permission-mode', 'default' if plain else 'auto', '--settings', json.dumps(settings)], timeout=90, label='delivery-' + case, keep=args.keep)
        events = record.pop('events')
        blocks = [b for event in events for b in message_blocks(event)]
        calls = [b for b in blocks if b.get('type') == 'tool_use' and b.get('name') == 'SubagentHandback']
        closing = [str(b.get('text', '')) for event in events if event.get('parent_tool_use_id') for b in message_blocks(event)
                   if b.get('type') == 'text' and '— NativePeers' in b.get('text', '')]
        reports = [str((c.get('input') or {}).get('message', '')) for c in calls] + closing
        hit_path = Path(record['scratch']) / 'tripwire.ndjson'
        traffic = [json.loads(line) for line in hit_path.read_text().splitlines()] if hit_path.exists() else []
        rows = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        shadow_requests = sum(str(row.get('model', '')).startswith('np-mirror') for row in traffic)
        expected = 0 if case in ('adopted', 'off', 'after', 'native-plain') else 1
        joined = '\n'.join(reports)
        checks = dict(process_success=record['exit_code'] == 0, no_shadow_model_request=shadow_requests == 0,
                      report_count=len(reports) == expected, handback_count=len(calls) == (0 if plain else expected),
                      adoption=any(row['kind'] == 'mirror.attach' and not row['body'].get('check_only') for row in rows) == (adopted or case == 'reattach'))
        if expected:checks['report_ids'] = 'npeer result G-' in joined and '— NativePeers' in joined
        if case in ('before', 'after'):
            routes = [row for row in rows if row['kind'] == 'report-route']
            checks['bounded_retry'] = sum(row['kind'] == 'mirror.surfacing' for row in rows) == 2
            checks['one_winning_transition'] = sum(row['changed'] and row['route'] == 'MIRROR_SURFACING' for row in routes) == 1
            checks['no_ack_after_lost_reply'] = case != 'after' or not any(row['kind'] == 'mirror.ack' for row in rows)
        if queued:
            checks['queued_disposition'] = [row['disposition'] for row in rows if row['kind'] == 'input-result'] == ['queued']
            surfacing = [row['body']['generation'] for row in rows if row['kind'] == 'mirror.surfacing']
            checks['folded_surfacing_order'] = bool(surfacing) and surfacing[0] == 2 and 1 in surfacing
            shadow_text = '\n'.join(str(b.get('text','')) for event in events if event.get('parent_tool_use_id') for b in message_blocks(event))
            if case.startswith('folded-native'):checks['folded_only_formatter'] = 'earlier task results' in joined
            else:checks['planner_fold_marker'] = 'completed; continuing' in shadow_text
            checks['one_forwarded_input'] = sum(row['kind'] == 'input' for row in rows) == 1
            if case.startswith('folded-native'):checks['only_earlier_mirror_report'] = 'Three easy wins' in joined and 'FOLD_SECOND' not in joined
            else:checks['only_second_report'] = 'FOLD_SECOND' in joined and 'Three easy wins' not in joined and 'Found 3 TODOs' not in joined
            omitted = 2 if case.startswith('folded-native') else 1
            checks['no_ack_for_omitted_task'] = not any(row['kind'] == 'mirror.ack' and row['body'].get('generation') == omitted for row in rows)
        if case == 'reattach':checks['backfill_reattached'] = sum(row['kind'] == 'mirror.attach' and not row['body'].get('check_only') for row in rows) == 1
        result = dict(case=case, checks=checks, passed=all(checks.values()), reports=len(reports), handbacks=len(calls), shadow_model_requests=shadow_requests,
                      classifier_requests_refused=len(traffic)-shadow_requests, harness=record)
        print(json.dumps(result, indent=2))
        if not result['passed']:raise SystemExit(1)


if __name__ == '__main__':main()
