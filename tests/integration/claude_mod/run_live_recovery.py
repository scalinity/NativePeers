"""Live recovery batch: a folded follow-up's delivery (AC4/AC7), a hot reload beside a running worker (AC10), and a
killed bridge recovered inside the step retry window and after its shadow faulted (AC7/AC8).

Live-test only, under the durable budget. No real parent turn: npguard scripts every main turn. The session loads a
copy of the mod so the reload edit never touches the repository. Bridges are found through the rig's bridge manifests.
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


def header(title):
    return '```np\n' + json.dumps({'model': 'gpt-6.1-sol', 'effort': 'low', 'title': title}) + '\n```\n'
SLEEPER = 'Run the shell command `sleep %d` and then reply with the single word: rested.'
def agent(title, prompt):
    return {'name': 'Agent', 'input': {'description': title, 'subagent_type': 'np:codex', 'prompt': header(title) + prompt}}
GUARD = {'on': {
    'launch the reviewer': [{'tools': [agent('Codex review', 'Run the shell command `sleep 30` first. Then read tally/count.py and report one easy win in one sentence. Do not edit any file.')]}, {'text': 'Launched.'}],
    'launch the reload sleeper': [{'tools': [agent('Reload sleeper', SLEEPER % 40)]}, {'text': 'Launched.'}],
    'launch the quick fault sleeper': [{'tools': [agent('Quick fault', SLEEPER % 45)]}, {'text': 'Launched.'}],
    'launch the slow fault sleeper': [{'tools': [agent('Slow fault', SLEEPER % 75)]}, {'text': 'Launched.'}],
}}
(RIG/'guard.json').write_text(json.dumps(GUARD))

opts = {'npeerPath': str(RIG/'npeer-wt')}
session = Session([MOD, HERE/'mods/turn_guard'], live=True, cwd=RIG/'fixture', keep=KEEP, label='live-recovery', cols=160, rows=55,
                  args=['--permission-mode', 'default', '--model', 'sonnet', '--settings', json.dumps({'pluginConfigs': {'np': {'options': opts}, 'np@inline': {'options': opts}}})],
                  env={'NPEER_HOME': str(RIG/'npeer-home'), 'NP_GUARD_REAL_TURNS': '0', 'NP_GUARD_LOG': str(KEEP/'guard.ndjson'), 'NP_GUARD_SCRIPT': str(RIG/'guard.json')})
marks = {}


def mark(name, value=None):
    marks[name] = time.time() if value is None else value


def snap(name, tail=3000):
    text = session.snap(name)
    print('##', name, round(time.time() - session.started, 1));print('\n'.join(l for l in text.splitlines() if l.strip() and '────' not in l)[-tail:]);sys.stdout.flush()
    return text


def to_main():
    if '↳ Subagent of main' in session.text():
        session.key('ctrl+x');session.key('tab');session.key('m');time.sleep(2)


def wait_index(title, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        to_main();session.type_line('/np');time.sleep(2.5)
        found = re.findall(r'(\d+)\. \S+ [^\n]*? · ' + re.escape(title) + r' · ', session.text())
        if found:return found[-1]
        time.sleep(3)
    return None


def open_worker(title, timeout=60):
    n = wait_index(title, timeout)
    if n:session.type_line(f'/np open {n}');time.sleep(3)
    return n


def bridges():
    out = []
    for path in sorted((RIG/'npeer-home'/'bridges').glob('*.json')):
        try:pid = json.loads(path.read_text())['process']['pid']
        except (OSError, ValueError, KeyError):continue
        if ' bridge --claude-session ' in subprocess.run(['ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True).stdout:out.append(pid)
    return out


def kill_bridges(name):
    killed = bridges()
    for pid in killed:os.kill(pid, signal.SIGKILL)
    mark(name, killed);mark(name + '-at')
    return killed


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
    mark('bridges_at_start', bridges())
    # AC4/AC7: a follow-up typed while the worker runs folds into the run; its one notification carries both generations.
    session.type_line('Please launch the reviewer.');mark('reviewer')
    open_worker('Codex review');session.type_line('Also name the function that has a TODO comment.');mark('ac4');time.sleep(4);snap('01-ac4-typed')
    session.wait_for(r'generations: 1, 2', timeout=240);mark('ac4-done');time.sleep(8);snap('02-ac4-done', 6000)
    # AC10: reload beside a running worker; the old instance keeps its bridge while a successor serves the new one.
    to_main();session.type_line('Please launch the reload sleeper.');wait_index('Reload sleeper', 60);time.sleep(6)
    mark('bridges_before_reload', bridges())
    with (MOD/'hooks/register.tsx').open('a') as f:f.write('\n// live reload marker\n')
    mark('reload');time.sleep(10);mark('bridges_after_reload', bridges());snap('03-after-reload')
    open_worker('Reload sleeper');session.wait_for(r'— NativePeers · codex', timeout=120);mark('reload-done');time.sleep(4);snap('04-reload-done', 4000)
    mark('bridges_after_reload_done', bridges())
    # AC7: the bridge dies; /np reattach inside the step retry window restarts it and the same shadow carries on.
    to_main();session.type_line('Please launch the quick fault sleeper.');wait_index('Quick fault', 60);time.sleep(8)
    kill_bridges('quick_killed');time.sleep(4)
    session.type_line('/np reattach');mark('quick-reattach');time.sleep(6);snap('05-quick-reattach')
    mark('bridges_after_quick', bridges())
    open_worker('Quick fault');session.wait_for(r'— NativePeers · codex|mirror fault', timeout=120);mark('quick-done');time.sleep(4);snap('06-quick-done', 4000)
    # AC8: the bridge stays down past the retry window; the shadow fails closed and /np reattach shows the worker again.
    to_main();session.type_line('Please launch the slow fault sleeper.');wait_index('Slow fault', 60);time.sleep(8)
    kill_bridges('slow_killed');time.sleep(30);snap('07-slow-faulted')
    session.type_line('/np reattach');mark('slow-reattach');time.sleep(8);snap('08-slow-reattach')
    mark('bridges_after_slow', bridges())
    # A display-only re-attach is a $.agent.spawn first run: it does not notify (X8), so its footer is read in its view.
    open_worker('Slow fault');session.wait_for(r'result retained in NativePeers|delivered via NativePeers', timeout=150);mark('slow-done');time.sleep(4);snap('09-slow-done', 4000)
    to_main();time.sleep(3);snap('10-final-band', 2500)
finally:
    mark('closed');(KEEP/'marks.json').write_text(json.dumps(marks))
    session.close()
    print('swept', session.swept)
