"""Observe native named/unnamed spawn roles through the documented replay harness."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import time
import subprocess

import harness
from harness import HERE, REPO, run


def agent(named=False):
    value = dict(subagent_type='np:codex', description='Spawn role fixture', prompt='Return the scripted report.')
    if named:value['name'] = 'named-review'
    return dict(name='Agent', input=value)


def teammate_calls(home, roles):
    members = [row for row in roles if row['type'] == 'npdriver:member' and row.get('agentId')]
    if not members:return []
    member = members[0]['agentId']
    calls, results = {}, {}
    # Read only this fixture's own teammate. Retain classifications, never its
    # transcript or prompt. Native guards may refuse before agent.spawn fires.
    for path in (home / '.claude/projects').rglob('agent-' + member + '.jsonl'):
        for line in path.read_text().splitlines():
            row = json.loads(line)
            blocks = row.get('message', {}).get('content', [])
            for block in blocks if isinstance(blocks, list) else []:
                if block.get('type') == 'tool_use' and block.get('name') == 'Agent' and block.get('input', {}).get('subagent_type') == 'np:codex':
                    calls[block['id']] = dict(type='teammate-call', caller=row.get('agentId'), name=block.get('input', {}).get('name'))
                if block.get('type') == 'tool_result':
                    text = str(block.get('content', ''))
                    if 'Teammates cannot spawn other teammates' in text:results[block['tool_use_id']] = 'NATIVE_TEAMMATE_NESTING_REFUSED'
                    elif 'In-process teammates cannot spawn background agents' in text:results[block['tool_use_id']] = 'NATIVE_BACKGROUND_AGENT_REFUSED'
    return [dict(call, disposition=results.get(key)) for key, call in calls.items()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=['main-named', 'main-unnamed', 'team-named', 'team-unnamed', 'teammate'], required=True)
    parser.add_argument('--keep', type=Path, required=True)
    parser.add_argument('--interactive', action='store_true', help='Use a PTY for a teams-disabled main case too')
    args = parser.parse_args()
    binary = harness.CLAUDE.resolve()
    harness.CLAUDE = binary
    with binary.open('rb') as stream:digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    with tempfile.TemporaryDirectory(prefix='np-version-') as version_home:
        version = subprocess.run([str(binary), '--version'], check=True, capture_output=True, text=True, timeout=15,
            env={'HOME': version_home, 'PATH': '/usr/bin:/bin', 'DISABLE_AUTOUPDATER': '1'}).stdout.strip()
    engine = dict(path=str(binary), sha256=digest, size=binary.stat().st_size, version=version)
    args.keep.mkdir(parents=True, exist_ok=True)
    roles = args.keep / 'roles'
    roles.mkdir()
    team = args.case.startswith('team')
    interactive = team or args.interactive
    teammate = args.case == 'teammate'
    with tempfile.TemporaryDirectory(prefix='np-spawn-roles-') as directory:
        script = dict(notifications='ack')
        if teammate:
            script['steps'] = [dict(tools=[dict(name='Agent', input=dict(subagent_type='npdriver:member', name='fixture-member', description='Scripted actual teammate', prompt='Run the fixture calls.'))]), dict(sleep_ms=8000, text='main fixture complete')]
            script['agents'] = {'npdriver:member': [dict(tools=[agent(True)]), dict(tools=[agent(False)]), dict(text='teammate fixture complete')]}
        else:
            script['steps'] = [dict(tools=[agent(args.case.endswith('-named'))]), dict(sleep_ms=4000, text='main fixture complete')]
        script_path = Path(directory) / 'script.json'
        script_path.write_text(json.dumps(script))
        opts = dict(npeerPath=str(HERE / 'bin/npeer-replay'), allowFromClaudeSubagents=True)
        settings = {'pluginConfigs': {'np': {'options': opts}, 'np@inline': {'options': opts}}}
        environment = {
            'NP_DRIVER_SCRIPT': str(script_path), 'NP_DRIVER_ALLOW_AGENT': '1', 'NP_DRIVER_SPAWN_LOG': str(roles),
            'NP_REPLAY_LOG': str(args.keep / 'replay.ndjson'), 'NP_REPLAY_SCENARIO': str(HERE / 'scenarios/codex_basic.json'),
        }
        if team:environment['CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS'] = '1'
        plugins = [HERE / 'driver', HERE / 'spawn_observer', REPO / 'integrations/claude/mod/np']
        if interactive:
            # Keep this import after the pin: pty_session binds harness.CLAUDE at import.
            from pty_session import Session
            session = Session(plugins, env=environment, args=['--permission-mode', 'default'], settings=settings, keep=args.keep, label=args.case)
            try:
                ready = session.wait_for('❯', timeout=35)
                session.type_line('go')
                deadline = time.monotonic() + 35
                while time.monotonic() < deadline:
                    observed = [json.loads(path.read_text()) for path in roles.glob('*.json')]
                    calls = teammate_calls(session.home, observed) if teammate else []
                    done = len(calls) == 2 and all(call['disposition'] for call in calls) if teammate else any(row['type'] == 'np:codex' for row in observed)
                    if done:break
                    time.sleep(.1)
                # Include any unexpected model fallthrough after the role check.
                time.sleep(2)
                record = dict(zero_api=not session.model_hits(), interactive=True, prompt_ready=ready, tripwire_hits=len(session.model_hits()))
                native_calls = teammate_calls(session.home, observed) if teammate else []
            finally:session.close()
            record['processes_found_at_close'] = session.swept
        else:
            record = run('go', plugins, env=environment, args=['--permission-mode', 'default', '--settings', json.dumps(settings)], timeout=60, keep=args.keep, label=args.case)
            record.pop('events')
    rows = [json.loads(path.read_text()) for path in roles.glob('*.json')]
    targets = native_calls if teammate else [row for row in rows if row['type'] == 'np:codex']
    replay_path = args.keep / 'replay.ndjson'
    replay = [json.loads(line) for line in replay_path.read_text().splitlines()] if replay_path.exists() else []
    spawns = [row for row in replay if row['kind'] == 'spawn' and not row.get('body', {}).get('check_only')]
    denied = teammate or args.case == 'team-named'
    checks = dict(native_start=record.get('prompt_ready', False) if interactive else record['exit_code'] == 0, zero_api=record['zero_api'],
        observed_calls=len(targets) == (2 if teammate else 1),
        backend_spawns=len(spawns) == (0 if denied else 1))
    if teammate:
        members = [row for row in rows if row['type'] == 'npdriver:member']
        checks['actual_teammate'] = len(members) == 1 and members[0]['isTeammate'] and bool(members[0].get('agentId'))
        checks['caller_is_actual_teammate'] = len(targets) == 2 and bool(members) and all(row.get('caller') == members[0].get('agentId') for row in targets)
        checks['both_denied'] = len(targets) == 2 and {row.get('name') is not None: row.get('disposition') for row in targets} == {True: 'NATIVE_TEAMMATE_NESTING_REFUSED', False: 'NATIVE_BACKGROUND_AGENT_REFUSED'}
    else:
        checks['call_shape'] = len(targets) == 1 and (targets[0].get('name') == 'named-review') == args.case.endswith('-named')
        checks['native_classification'] = len(targets) == 1 and targets[0]['isTeammate'] == denied and not targets[0].get('parentAgentId')
        checks['disposition'] = len(targets) == 1 and (bool(targets[0].get('deny')) if denied else bool(targets[0].get('agentId')))
    if denied and not teammate:checks['actionable_refusal'] = bool(targets) and all('unnamed Agent(np:*) from the main conversation' in row.get('deny', '') and 'npeer spawn' in row.get('deny', '') for row in targets)
    with binary.open('rb') as stream:checks['engine_unchanged'] = hashlib.file_digest(stream, 'sha256').hexdigest() == digest
    result = dict(case=args.case, engine=engine, checks=checks, passed=all(checks.values()), roles=rows, native_calls=targets if teammate else [], harness=record)
    (args.keep / 'qualification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    if not result['passed']:raise SystemExit(1)


if __name__ == '__main__':main()
