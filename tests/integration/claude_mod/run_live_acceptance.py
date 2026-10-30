"""Live acceptance batch: AC1/AC2 fan-out, AC4/AC5 follow-ups, AC10 reload, AC6 stops, AC12 adoption, AC7/AC8 bridge death.

Live-test only, under the durable budget. One real parent turn (the four-provider fan-out). Later turns are
npguard's: scripted plans for the sleepers, the adoption and the fault worker, local answers for
notifications. The session loads a copy of the mod so the reload edit never touches the repository.
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
shutil.copytree(HERE.parents[2]/'integrations/claude/mod/np', MOD, ignore=shutil.ignore_patterns('types', '.claude-plugin/types'))
shutil.copytree(HERE.parents[2]/'integrations/claude/mod/np/types', MOD/'types')
(RIG/'bin').mkdir(exist_ok=True)
if not (RIG/'bin/npeer').exists():(RIG/'bin/npeer').symlink_to(RIG/'npeer-wt')
(RIG/'adopt-spec.json').write_text(json.dumps({'schema_version': 1, 'client_request_id': 'live-adopt-1', 'completion_mode': 'push',
    'workers': [{'provider': 'codex', 'title': 'Adopted worker', 'model': 'gpt-6.1-sol', 'reasoning_effort': 'low', 'permission_profile': 'analysis',
                 'workspace': {'mode': 'inherit-read-only'}, 'task': 'Read README.md and reply with its first line only. Do not edit any file.'}],
    'limits': {'max_concurrent_workers': 1, 'max_depth': 1, 'wall_time_seconds': 600, 'max_task_turns_per_worker': 4}}))

HEADER = '```np\n{"model":"gpt-6.1-sol","effort":"low","title":"%s"}\n```\nRun the shell command `sleep %d` and then reply with the single word: rested.'
GUARD = {'on': {
    'launch two sleepers': [{'text': 'Launching two sleepers.', 'tools': [
        {'name': 'Agent', 'input': {'description': 'Sleeper A', 'subagent_type': 'np:codex', 'prompt': HEADER % ('Sleeper A', 50)}},
        {'name': 'Agent', 'input': {'description': 'Sleeper B', 'subagent_type': 'np:codex', 'prompt': HEADER % ('Sleeper B', 50)}}]}, {'text': 'Launched.'}],
    'launch the third sleeper': [{'tools': [{'name': 'Agent', 'input': {'description': 'Sleeper C', 'subagent_type': 'np:codex', 'prompt': HEADER % ('Sleeper C', 60)}}]}, {'text': 'Launched.'}],
    'adopt a worker': [{'tools': [{'name': 'Bash', 'input': {'command': f'{RIG}/bin/npeer spawn --spec {RIG}/adopt-spec.json --json', 'description': 'Spawn a worker with npeer'}}]}, {'text': 'Spawned.'}],
    'launch the fault sleeper': [{'tools': [{'name': 'Agent', 'input': {'description': 'Fault test', 'subagent_type': 'np:codex', 'prompt': HEADER % ('Fault test', 70)}}]}, {'text': 'Launched.'}],
}}
(RIG/'guard.json').write_text(json.dumps(GUARD))
FANOUT = ('Spin up four NativePeers delegates at once, one Agent call each, all in the same response:\n'
          '- np:codex with the header ```np {"model":"gpt-6.1-sol","effort":"high","title":"Codex review"}```\n'
          '- np:grok with the header ```np {"model":"grok-4.7","title":"Grok review"}```\n'
          '- np:antigravity with the header ```np {"model":"gemini-4-argon-high","title":"Gemini review"}```\n'
          '- np:claude with the header ```np {"model":"sonnet","title":"Claude review"}```\n'
          'Put each header on its own lines at the start of its prompt, then this brief: "Read tally/count.py in the current repository and report '
          'one easy win in one sentence. Do not edit any file." End your turn after launching them; do not wait.')

opts = {'npeerPath': str(RIG/'npeer-wt')}
session = Session([MOD, HERE/'mods/turn_guard'], live=True, cwd=RIG/'fixture', keep=KEEP, label='live-acceptance', cols=160, rows=55,
                  args=['--permission-mode', 'default', '--model', 'sonnet', '--settings', json.dumps({'pluginConfigs': {'np': {'options': opts}, 'np@inline': {'options': opts}}})],
                  env={'NPEER_HOME': str(RIG/'npeer-home'), 'NP_GUARD_REAL_TURNS': '1', 'NP_GUARD_LOG': str(KEEP/'guard.ndjson'), 'NP_GUARD_SCRIPT': str(RIG/'guard.json')})
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
    to_main();session.type_line('/np');time.sleep(3)
    found = re.findall(r'(\d+)\. \S+ [^\n]*? · ' + re.escape(title) + r' · ', session.text())
    return found[-1] if found else None


def open_worker(title):
    n = index(title)
    if n:session.type_line(f'/np open {n}');time.sleep(3)
    return n


def wait_band(pattern, timeout):
    to_main();return session.wait_for(pattern, timeout=timeout)


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
    # AC1/AC2: the real parent turn.
    session.type_line(FANOUT);mark('fanout')
    session.wait_for(r'np:claude[\s\S]*np:|np:grok[\s\S]*np:', timeout=150);time.sleep(6);mark('rows');snap('01-fanout-rows')
    # AC4: a follow-up typed while the Codex worker runs.
    open_worker('Codex review');snap('02-view-codex-running')
    session.type_line('Also name the function that has a TODO comment.');mark('ac4');time.sleep(4);snap('03-ac4-typed')
    wait_band(r'4 delegates · 0 running', 420);mark('fanout-done');snap('04-fanout-done')
    for title in ('Codex review', 'Grok review', 'Gemini review', 'Claude review'):
        open_worker(title);time.sleep(2);snap('05-view-' + title.split()[0].lower(), 6000)
    # AC5: a follow-up to a completed worker resumes the same native conversation.
    open_worker('Grok review');session.type_line('Now suggest one test name for that easy win.');mark('ac5');time.sleep(5);snap('06-ac5-typed')
    session.wait_for(r'generation 2', timeout=240);time.sleep(20);snap('07-ac5-done', 6000)
    to_main()
    # AC10 reload and AC6 interrupt / Esc-stop.
    session.type_line('Please launch two sleepers.');mark('sleepers');session.wait_for(r'Sleeper B', timeout=90);time.sleep(8);snap('08-sleepers')
    with (MOD/'hooks/register.tsx').open('a') as f:f.write('\n// live reload marker\n')
    mark('reload');time.sleep(8);snap('09-after-reload')
    a = index('Sleeper A')
    if a:session.type_line(f'/np interrupt {a}');mark('interrupt');time.sleep(6);snap('10-interrupt')
    open_worker('Sleeper B');time.sleep(2);session.key('esc');mark('esc-stop');time.sleep(6);snap('11-esc-stop')
    to_main();time.sleep(4);snap('12-after-stops')
    # AC6 Stop all.
    session.type_line('Please launch the third sleeper.');mark('third');session.wait_for(r'Sleeper C', timeout=90);time.sleep(8)
    session.type_line('/np stop all');mark('stop-all');time.sleep(10);snap('13-stop-all')
    # AC12 adoption through npeer spawn.
    session.type_line('Please adopt a worker.');mark('adopt');session.wait_for(r'Adopted worker', timeout=120);time.sleep(5);snap('14-adopted')
    session.wait_for(r'delivered via NativePeers', timeout=240);time.sleep(15);snap('15-adopted-done', 4000)
    # AC7/AC8: the bridge dies while a mirror-owned worker runs; re-attach restores mirroring.
    to_main();session.type_line('Please launch the fault sleeper.');mark('fault');session.wait_for(r'Fault test', timeout=90);time.sleep(10)
    out = subprocess.run(['ps', '-axeww', '-o', 'pid=,command='], capture_output=True, text=True).stdout
    bridges = [int(l.split(None, 1)[0]) for l in out.splitlines() if session.marker in l and ' bridge --claude-session ' in l]
    for pid in bridges:os.kill(pid, signal.SIGKILL)
    mark('bridge-killed');marks['bridge_pids'] = bridges
    open_worker('Fault test');time.sleep(40);snap('16-fault-view')
    to_main();session.type_line('/np reattach');mark('reattach');time.sleep(10);snap('17-reattach')
    session.wait_for(r'rested', timeout=120);time.sleep(10);snap('18-reattach-done', 4000)
    to_main();session.type_line('/cost');time.sleep(4);snap('19-cost', 2500)
finally:
    mark('closed');(KEEP/'marks.json').write_text(json.dumps(marks))
    session.close()
    print('swept', session.swept)
