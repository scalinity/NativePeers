#!/opt/homebrew/bin/python3
"""Small native tool/hook probes. No global registration or model launcher."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from nativepeers.files import atomic_json, private_directory
from nativepeers.process import ancestry, identity, matches, signal_owned
from nativepeers.budget import LiveBudget


def owner(provider):
    allowed = (Path.home()/'.local/share/codex-mcp-manager/current/bin/codex').resolve() if provider == 'codex' else (Path.home()/'.local/bin/claude').resolve()
    candidates = [p for p in ancestry()[1:] if p.executable == str(allowed)]
    if not candidates:
        raise RuntimeError('CALLER_UNBOUND: no exact native owner in process ancestry')
    return candidates[0]


def hook(args):
    payload = json.loads(sys.stdin.buffer.read(65537))
    session = str(uuid.UUID(payload['session_id']))
    native_owner = owner(args.provider)
    record = dict(native_id=session, owner=native_owner.json(), incarnation_id=str(uuid.uuid4()),
                  provider=args.provider, hook_event=payload['hook_event_name'], cwd=payload['cwd'],
                  socket=os.environ.get('CLAUDE_CODE_MESSAGING_SOCKET'), at=time.time())
    atomic_json(args.run/'hook.json',record)
    env_file=os.getenv('CLAUDE_ENV_FILE')
    if env_file:
        with open(env_file,'a') as f:
            f.write(f"\nexport NPEER_PROBE_BINDING='{args.run}/hook.json'\n")


def context(args):
    record = json.loads((args.run/'hook.json').read_text())
    native_owner = owner(args.provider)
    if record['owner'] != native_owner.json() or not matches(record['owner']):
        raise RuntimeError('CALLER_UNBOUND: owner differs from native hook')
    if args.provider == 'codex' and os.environ.get('CODEX_THREAD_ID') != record['native_id']:
        raise RuntimeError('CALLER_UNBOUND: tool thread differs from native hook')
    private_directory(args.run)
    tool = dict(record, tool_ancestry=[p.json() for p in ancestry()],
                tool_call_id=os.environ.get('CODEX_TOOL_CALL_ID'), terminal_fds=[os.isatty(i) for i in range(3)])
    atomic_json(args.run/'tool.json',tool)
    process = subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'helper','--provider',args.provider,'--run',str(args.run)],
                               stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                               close_fds=True,start_new_session=True)
    deadline=time.monotonic()+4
    while not (args.run/'ready.json').exists() and time.monotonic()<deadline:
        if process.poll() is not None:raise RuntimeError('Helper exited before readiness')
        time.sleep(.02)
    if not (args.run/'ready.json').exists():raise TimeoutError('Helper readiness unknown; inspect manifest before retry')
    print(json.dumps(dict(ok=True,native_id=record['native_id'],incarnation_id=record['incarnation_id'],helper_pid=process.pid)))


def helper(args):
    record=json.loads((args.run/'tool.json').read_text())
    read_fd,write_fd=os.pipe()
    child=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'lease-child','--lease-fd',str(read_fd)],
                            pass_fds=(read_fd,),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                            close_fds=True,start_new_session=True)
    os.close(read_fd)
    own=identity(os.getpid()).json(); child_owner=identity(child.pid).json()
    atomic_json(args.run/'ready.json',dict(helper=own,child=child_owner,terminal_fds=[os.isatty(i) for i in range(3)],at=time.time()))
    time.sleep(5)
    atomic_json(args.run/'survived.json',dict(helper=own,owner_alive=matches(record['owner']),child_alive=child.poll() is None,at=time.time()))
    os.close(write_fd)
    try:child.wait(timeout=3)
    except subprocess.TimeoutExpired:
        signal_owned(child_owner);child.wait(timeout=2)
    atomic_json(args.run/'lease-cleanup.json',dict(child=child_owner,exit_code=child.returncode,at=time.time()))
    if not matches(record['owner']):return
    budget=LiveBudget(ROOT/'evidence/live-budget.json')
    reservation=json.loads((args.run/'return-reservation.json').read_text())['id']
    text=f"NativePeers probe completion {args.run.name}. Reply exactly RETURN {args.run.name}. This is a scoped non-model helper completion; do not run tools or delegate."
    budget.admit(reservation)
    if args.provider=='codex':
        binary=str((Path.home()/'.local/share/codex-mcp-manager/current/bin/codex').resolve())
        completed=subprocess.run([binary,'queue','--thread',record['native_id'],'--message',text],stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=20)
        atomic_json(args.run/'submission.json',dict(returncode=completed.returncode,stdout=completed.stdout,stderr=completed.stderr,at=time.time()))
    else:
        endpoint=record['socket']
        if endpoint.startswith('uds:'):endpoint=endpoint[4:]
        frame=dict(msgV=1,msg_id=str(uuid.uuid4()),type='user',session_id=record['native_id'],priority='next',message=dict(role='user',content=text))
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
            client.settimeout(3);client.connect(endpoint);client.sendall((json.dumps(frame)+'\n').encode());client.shutdown(socket.SHUT_WR)
            time.sleep(1)
        atomic_json(args.run/'submission.json',dict(state='SUBMITTED_UNCONFIRMED',frame=frame,at=time.time(),no_auth_token=True))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['hook','context','helper','lease-child'])
    parser.add_argument('--provider',choices=['claude','codex'])
    parser.add_argument('--run',type=Path)
    parser.add_argument('--lease-fd',type=int)
    args=parser.parse_args()
    if args.action=='lease-child':
        os.read(args.lease_fd,1);os.close(args.lease_fd);return
    private_directory(args.run)
    {'hook':hook,'context':context,'helper':helper}[args.action](args)


if __name__=='__main__':main()
