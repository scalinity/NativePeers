"""Exercise report ownership in the documented headless replay harness.

Auto mode's classifier reaches the refusing tripwire; those calls are reported
separately. No np-mirror model request is allowed. Native delivery itself is
covered by test_mirror_routes; this fixture checks the actual shadow handback.
"""
import argparse
import json
from pathlib import Path
import tempfile

from harness import HERE, REPO, run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=['adopted', 'direct', 'off'], required=True)
    parser.add_argument('--keep', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='np-delivery-') as directory:
        directory = Path(directory)
        direct = args.case == 'direct'
        script = directory / 'driver.json'
        script.write_text(json.dumps({'notifications': 'ack', 'steps': [
            {'tools': [{'name': 'Agent', 'input': {'subagent_type': 'np:codex', 'description': 'Ownership fixture', 'prompt': 'Return the scripted fixture report.'}}]} if direct else
            {'tools': [{'name': 'Bash', 'input': {'command': 'echo npeer spawn --spec fixture.json --json', 'description': 'Replay adoption trigger'}}]},
            {'sleep_ms': 8000, 'text': 'main fixture complete'}]}))
        log = directory / 'replay.ndjson'
        opts = {'npeerPath': str(HERE / 'bin/npeer-replay'), 'adoption': 'off' if args.case == 'off' else 'auto'}
        settings = {'permissions': {'allow': ['Bash(echo:*)']}, 'pluginConfigs': {'np': {'options': opts}, 'np@inline': {'options': opts}}}
        record = run('go', [HERE / 'driver', REPO / 'integrations/claude/mod/np'],
                     env={'NP_DRIVER_SCRIPT': str(script), 'NP_DRIVER_ALLOW_AGENT': '1', 'NP_REPLAY_LOG': str(log),
                          'NP_REPLAY_SCENARIO': str(HERE / 'scenarios' / ('codex_basic.json' if direct else 'adopt_grok.json'))},
                     args=['--permission-mode', 'auto', '--settings', json.dumps(settings)], timeout=90, label='delivery-' + args.case, keep=args.keep)
        calls = [b for event in record.pop('events') for b in (event.get('message') or {}).get('content', [])
                 if isinstance(b, dict) and b.get('type') == 'tool_use' and b.get('name') == 'SubagentHandback']
        hit_path = Path(record['scratch']) / 'tripwire.ndjson'
        traffic = [json.loads(line) for line in hit_path.read_text().splitlines()] if hit_path.exists() else []
        rows = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        shadow_requests = sum(str(row.get('model', '')).startswith('np-mirror') for row in traffic)
        report = str((calls[0].get('input') or {}).get('message', '')) if calls else ''
        checks = dict(process_success=record['exit_code'] == 0, no_shadow_model_request=shadow_requests == 0,
                      handback_count=len(calls) == (1 if direct else 0),
                      correct_report=('npeer result G-' in report and '— NativePeers' in report) if direct else not report,
                      adoption=(any(row['kind'] == 'mirror.attach' and not row['body'].get('check_only') for row in rows)) == (args.case == 'adopted'))
        result = dict(case=args.case, checks=checks, passed=all(checks.values()), handbacks=len(calls), shadow_model_requests=shadow_requests,
                      classifier_requests_refused=len(traffic)-shadow_requests, harness=record)
        print(json.dumps(result, indent=2))
        if not result['passed']:raise SystemExit(1)


if __name__ == '__main__':main()
