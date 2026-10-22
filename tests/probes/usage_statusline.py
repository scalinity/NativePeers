"""Opt-in native Claude status-line contract check. Never writes prompt/keystroke input."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import shlex
import signal
import struct
import subprocess
import sys
import termios
import time
import uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def receive(output,expected):
    from nativepeers.process import ancestry
    from nativepeers.files import atomic_json
    raw=sys.stdin.buffer.read(262145)
    if len(raw)>262144:raise RuntimeError('Oversized native status-line payload')
    value=json.loads(raw)
    owners=[p.json() for p in ancestry()[1:]]
    if not any(p['executable']==expected for p in owners):raise RuntimeError('No exact native owner')
    uuid.UUID(value['session_id'])
    if not isinstance(value.get('model'),dict) or not isinstance(value.get('version'),str):raise RuntimeError('Unexpected native envelope')
    from nativepeers.usage.normalize import claude
    atomic_json(output,dict(native_version=value['version'],model=value['model'].get('id'),
        native_id=value['session_id'],envelope_keys=sorted(value),quota_pools=claude(value),
        exact_native_owner=True,quota_present=isinstance(value.get('rate_limits'),dict)))


def run(directory,state,timeout,record):
    from nativepeers.compatibility import fingerprint
    from nativepeers.files import atomic_json,private_directory,advisory_lock
    from nativepeers.launch import launch_environment
    from nativepeers.process import identity,signal_owned,matches
    directory=private_directory(directory.resolve());output=directory/'native-statusline.json'
    if output.exists():raise RuntimeError('Use a fresh probe directory')
    binary=(Path.home()/'.local/bin/claude').resolve(strict=True);runtime=fingerprint(binary)
    # Source inspection is restricted to named public contract literals.
    data=binary.read_bytes();literals={s:s.encode() in data for s in ('rate_limits','five_hour','seven_day','used_percentage','resets_at')};del data
    if not all(literals.values()):raise RuntimeError('Required documented quota contract literals are absent')
    settings=directory/'settings.json'
    command=shlex.join([str(ROOT/'.venv/bin/python'),str(Path(__file__).resolve()),'--receive',str(output),'--expected-executable',str(binary)])
    atomic_json(settings,{'statusLine':{'type':'command','command':command}})
    master,slave=pty.openpty();fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',40,120,0,0))
    environment=launch_environment('claude');environment['TERM']='xterm-256color'
    process=subprocess.Popen([str(binary),'--settings',str(settings)],cwd=directory,env=environment,
        stdin=slave,stdout=slave,stderr=slave,start_new_session=True,close_fds=True)
    os.close(slave);owner=identity(process.pid).json();tail=bytearray();deadline=time.monotonic()+timeout
    try:
        while time.monotonic()<deadline and process.poll() is None and not output.exists():
            if select.select([master],[],[],.1)[0]:
                try:tail.extend(os.read(master,8192));del tail[:-65536]
                except OSError:break
    finally:
        if process.poll() is None:
            signal_owned(owner,signal.SIGTERM,group=True)
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                signal_owned(owner,signal.SIGKILL,group=True);process.wait(timeout=2)
        os.close(master)
        # Terminal output may contain local paths; it stays private.
        (directory/'terminal.log').write_bytes(tail);(directory/'terminal.log').chmod(0o600)
    result=dict(status='PASS' if output.exists() else 'NOT_RUN',runtime=runtime,
        contract_literals=literals,provider_exited=not matches(owner),submitted_inputs=0,
        scope='Native zero-input status-line envelope only; quota values may be absent until ordinary inference. Worker/inbox acceptance unchanged.')
    if output.exists():result['native']=json.loads(output.read_text())
    atomic_json(directory/'result.json',result)
    if record and result['status']=='PASS':
        if fingerprint(binary)!=runtime:raise RuntimeError('Native binary changed during its contract probe')
        path=state/'compat/usage-runtime.json'
        with advisory_lock(state/'locks/usage-contract.lock'):
            receipt=json.loads(path.read_text()) if path.exists() else {}
            receipt['claude']=dict(contract='native-statusline-v1',proof='verified-zero-input-native-statusline',
                runtime=runtime,native_version=result['native']['native_version'],verified_at=time.time())
            atomic_json(path,receipt)
    print(json.dumps({k:v for k,v in result.items() if k!='native'},indent=2))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--receive',type=Path);parser.add_argument('--expected-executable')
    parser.add_argument('--directory',type=Path);parser.add_argument('--state',type=Path,default=Path.home()/'.local/state/nativepeers')
    parser.add_argument('--timeout',type=float,default=15);parser.add_argument('--record',action='store_true');args=parser.parse_args()
    if args.receive:receive(args.receive,args.expected_executable)
    else:
        if not args.directory or not 1<=args.timeout<=30:parser.error('Choose a fresh private directory and a timeout from 1 to 30 seconds')
        run(args.directory,args.state,args.timeout,args.record)


if __name__=='__main__':main()
