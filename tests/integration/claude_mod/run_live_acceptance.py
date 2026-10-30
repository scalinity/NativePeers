"""Live acceptance batch: AC1/AC2 fan-out, AC5/AC4 follow-ups, AC10 reload, AC6 stops, AC12 adoption, AC7/AC8 bridge death.

Live-test only, under the durable budget. No real parent turn: every main turn is npguard's (scripted plans for the
fan-out, the sleepers, the adoption and the fault worker; local answers for notifications). The worktree rig cannot
run Grok (its installed hooks name the main checkout and the production state root), so the fan-out is Codex,
Antigravity and Claude; the four-provider fan-out is the post-merge production check. The session loads a copy of
the mod so the reload edit never touches the repository.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pty_session import Session

HERE = Path(__file__).resolve().parent
RIG = Path(sys.argv[1]).resolve()
KEEP = Path(sys.argv[2]).resolve()
KEEP.mkdir(parents=True, exist_ok=True)
MOD = RIG/'mod-np'
if MOD.exists():shutil.rmtree(MOD)
shutil.copytree(HERE.parents[2]/'integrations/claude/mod/np', MOD)
(RIG/'bin').mkdir(exist_ok=True)
if not (RIG/'bin/npeer').exists():(RIG/'bin/npeer').symlink_to(RIG/'npeer-wt')
(RIG/'adopt-spec.json').write_text(json.dumps({'schema_version': 1, 'client_request_id': 'live-adopt-1', 'completion_mode': 'push',
    'workers': [{'provider': 'codex', 'title': 'Adopted worker', 'model': 'gpt-6.1-sol', 'reasoning_effort': 'low', 'permission_profile': 'analysis',
                 'workspace': {'mode': 'inherit-read-only'}, 'task': 'Read README.md and reply with its first line only. Do not edit any file.'}],
    'limits': {'max_concurrent_workers': 1, 'max_depth': 1, 'wall_time_seconds': 600, 'max_task_turns_per_worker': 4}}))

def header(model, title, effort=None):
    return '```np\n' + json.dumps({'model': model, **({'effort': effort} if effort else {}), 'title': title}) + '\n```\n'
REVIEW = 'Read tally/count.py in the current repository and report one easy win in one sentence. Do not edit any file.'
SLEEPER = 'Run the shell command `sleep %d` and then reply with the single word: rested.'
def agent(provider, prompt, description):
    return {'name': 'Agent', 'input': {'description': description, 'subagent_type': 'np:' + provider, 'prompt': prompt}}
GUARD = {'on': {
    'fan out three': [{'text': 'Fanning out to three providers.', 'tools': [
        agent('codex', header('gpt-6.1-sol', 'Codex review', 'low') + 'Run the shell command `sleep 60` first. Then: ' + REVIEW, 'Codex review'),
        agent('antigravity', header('gemini-4-argon-high', 'Gemini review') + REVIEW, 'Gemini review'),
        agent('claude', header('sonnet', 'Claude review') + REVIEW, 'Claude review')]}, {'text': 'Launched.'}],
    'launch two sleepers': [{'text': 'Launching two sleepers.', 'tools': [
        agent('codex', header('gpt-6.1-sol', 'Sleeper A', 'low') + SLEEPER % 50, 'Sleeper A'),
        agent('codex', header('gpt-6.1-sol', 'Sleeper B', 'low') + SLEEPER % 50, 'Sleeper B')]}, {'text': 'Launched.'}],
    'launch the third sleeper': [{'tools': [agent('codex', header('gpt-6.1-sol', 'Sleeper C', 'low') + SLEEPER % 60, 'Sleeper C')]}, {'text': 'Launched.'}],
    'adopt a worker': [{'tools': [{'name': 'Bash', 'input': {'command': f'{RIG}/bin/npeer spawn --spec {RIG}/adopt-spec.json --json', 'description': 'Spawn a worker with npeer'}}]}, {'text': 'Spawned.'}],
    'launch the fault sleeper': [{'tools': [agent('codex', header('gpt-6.1-sol', 'Fault test', 'low') + SLEEPER % 60, 'Fault test')]}, {'text': 'Launched.'}],
}}
(RIG/'guard.json').write_text(json.dumps(GUARD))

opts = {'npeerPath': str(RIG/'npeer-wt')}
session = Session([MOD, HERE/'mods/turn_guard'], live=True, cwd=RIG/'fixture', keep=KEEP, label='live-acceptance', cols=160, rows=55,
                  args=['--permission-mode', 'default', '--model', 'sonnet', '--allowedTools', f'Bash({RIG}/bin/npeer spawn:*)',
                        '--settings', json.dumps({'pluginConfigs': {'np': {'options': opts}, 'np@inline': {'options': opts}}})],
                  env={'NPEER_HOME': str(RIG/'npeer-home'), 'NP_GUARD_REAL_TURNS': '0', 'NP_GUARD_LOG': str(KEEP/'guard.ndjson'), 'NP_GUARD_SCRIPT': str(RIG/'guard.json')})
marks = {}


def mark(name):
    marks[name] = time.time()


def snap(name, tail=3000):
    text = session.snap(name)
    print('##', name, round(time.time() - session.started, 1));print('\n'.join(l for l in text.splitlines() if l.strip() and '────' not in l)[-tail:]);sys.stdout.flush()
    return text


def to_main():
    if '↳ Subagent of main' in session.text():
        session.key('ctrl+x');session.key('tab');session.key('m');time.sleep(2)


def index(title):
    to_main();session.type_line('/np');time.sleep(2.5)
    found = re.findall(r'(\d+)\. \S+ [^\n]*? · ' + re.escape(title) + r' · ', session.text())
    return found[-1] if found else None


def wait_index(title, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        n = index(title)
        if n:return n
        time.sleep(3)
    return None


def open_worker(title, timeout=60):
    n = wait_index(title, timeout)
    if n:session.type_line(f'/np open {n}');time.sleep(3)
    return n


def bridges():
    # Engine-spawned processes do not inherit the session's marker; the rig's bridge manifests record each bridge.
    out = []
    for path in sorted((RIG/'npeer-home'/'bridges').glob('*.json')):
        try:pid = json.loads(path.read_text())['process']['pid']
        except (OSError, ValueError, KeyError):continue
        if ' bridge --claude-session ' in subprocess.run(['ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True).stdout:out.append(pid)
    return out


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
    marks['bridges_at_start'] = bridges()
    # AC1/AC2: three providers at once (a scripted main turn).
    session.type_line('Please fan out three reviewers.');mark('fanout')
    session.wait_for(r'np:claude[\s\S]*np:|np:antigravity[\s\S]*np:', timeout=60);time.sleep(3);mark('rows');snap('01-fanout-rows')
    # AC5: hold the Claude worker's view through its completion, then follow up while it is still listed.
    open_worker('Claude review');snap('02-view-claude')
    session.wait_for(r'— NativePeers · claude', timeout=180);mark('claude-done');snap('03-claude-done', 6000)
    session.type_line('Now suggest one test name for that easy win.');mark('ac5');time.sleep(5);snap('04-ac5-typed')
    session.wait_for(r'generations: 1, 2', timeout=150);mark('ac5-done');time.sleep(3);snap('04b-ac5-done', 6000)
    # AC4: a follow-up typed while the Codex worker still runs its sleep.
    open_worker('Codex review');snap('05-view-codex-running')
    session.type_line('Also name the function that has a TODO comment.');mark('ac4');time.sleep(5);snap('06-ac4-typed')
    session.wait_for(r'generations: 1, 2', timeout=300);mark('ac4-done');time.sleep(4);snap('07-ac4-done', 6000)
    to_main();time.sleep(2);snap('09-fanout-band', 2500)
    # AC10 reload with two running sleepers, then AC6 interrupt and Esc-stop through the reloaded module.
    session.type_line('Please launch two sleepers.');mark('sleepers');session.wait_for(r'Sleeper B', timeout=60)
    wait_index('Sleeper B', 60);time.sleep(6);snap('10-sleepers')
    marks['bridges_before_reload'] = bridges()
    with (MOD/'hooks/register.tsx').open('a') as f:f.write('\n// live reload marker\n')
    mark('reload');time.sleep(10);marks['bridges_after_reload'] = bridges();snap('11-after-reload')
    a = wait_index('Sleeper A', 30)
    if a:session.type_line(f'/np interrupt {a}');mark('interrupt');time.sleep(8);snap('12-interrupt')
    open_worker('Sleeper B');time.sleep(2);session.key('esc');mark('esc-stop');time.sleep(8);snap('13-esc-stop')
    to_main();time.sleep(4);snap('14-after-stops')
    # AC6 Stop all.
    session.type_line('Please launch the third sleeper.');mark('third');session.wait_for(r'Sleeper C', timeout=60);wait_index('Sleeper C', 60);time.sleep(6)
    session.type_line('/np stop all');mark('stop-all');time.sleep(12);snap('15-stop-all')
    marks['bridges_after_stops'] = bridges()
    # AC12 adoption through npeer spawn.
    to_main();session.type_line('Please adopt a worker.');mark('adopt');session.wait_for(r'Adopted worker', timeout=120);time.sleep(5);snap('16-adopted')
    session.wait_for(r'delivered via NativePeers', timeout=240);mark('adopt-done');time.sleep(10);snap('17-adopted-done', 4000)
    # AC7/AC8: the bridge dies while a mirror-owned worker runs; /np reattach inside the step retry window restores it.
    to_main();session.type_line('Please launch the fault sleeper.');mark('fault');session.wait_for(r'Fault test', timeout=60);wait_index('Fault test', 60);time.sleep(8)
    killed = bridges()
    for pid in killed:os.kill(pid, signal.SIGKILL)
    mark('bridge-killed');marks['bridge_pids_killed'] = killed;time.sleep(4)
    session.type_line('/np reattach');mark('reattach');time.sleep(8);snap('18-reattach')
    marks['bridges_after_reattach'] = bridges()
    open_worker('Fault test');session.wait_for(r'— NativePeers · codex|mirror fault', timeout=150);mark('fault-done');time.sleep(6);snap('19-fault-done', 4000)
    to_main();time.sleep(3);snap('20-final-band', 2500)
    session.type_line('/cost');time.sleep(4);snap('21-cost', 2500)
finally:
    mark('closed');(KEEP/'marks.json').write_text(json.dumps(marks))
    session.close()
    print('swept', session.swept)
