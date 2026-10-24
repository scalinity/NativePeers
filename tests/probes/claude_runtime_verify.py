#!/usr/bin/env python3
"""Explicit Claude worker/inbox renewal in disposable state, then guarded approval.

Run inspect, review the executable change, prepare --reviewed-sha256 HASH,
launch in an owned PTY, collect, and approve. Nothing approves on installation.
"""
import argparse
import asyncio
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from nativepeers.caller import bind_caller
from nativepeers.compatibility import fingerprint, update_verified
from nativepeers.core import Core
from nativepeers.errors import require
from nativepeers.files import atomic_json, private_directory
from nativepeers.journal import Journal
from nativepeers.launch import launch_environment
from nativepeers.models import new_id


def stamp():return datetime.now(ZoneInfo('America/New_York')).isoformat()


def sources():
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT/'nativepeers').rglob('*.py'))}


def read(run, name):return json.loads((run/name).read_text())


def inspect(run):
    require(not (run/'prepared.json').exists(),'OWNERSHIP_CONFLICT','Prepared qualification is immutable; use a fresh run directory')
    binary=Path.home()/'.local/bin/claude'
    actual=fingerprint(binary)
    version=subprocess.run([actual['path'],'--version'],capture_output=True,text=True,timeout=15,check=True)
    help_result=subprocess.run([actual['path'],'--help'],capture_output=True,text=True,timeout=15,check=True)
    required=['--output-format','--session-id','--resume','--tools','--allowedTools','--strict-mcp-config','--permission-mode']
    require(all(flag in help_result.stdout for flag in required),'CAPABILITY_MISMATCH','Required Claude flags are absent')
    state=Path(os.environ.get('NPEER_HOME',Path.home()/'.local/state/nativepeers')).resolve()
    receipt=json.loads((state/'compat/runtime.json').read_text())
    record=dict(at=stamp(),runtime=actual,version=version.stdout.strip(),daily_state=str(state),
                expected_approval=receipt['providers']['claude'],source=sources(),
                help_sha256=hashlib.sha256(help_result.stdout.encode()).hexdigest(),model_input=False)
    (run/'help.txt').write_text(help_result.stdout);atomic_json(run/'inspect.json',record)
    return record


def prepare(run, reviewed):
    inspected=read(run,'inspect.json')
    require(reviewed==inspected['runtime']['sha256'],'CAPABILITY_MISMATCH','Review the exact inspected fingerprint')
    require(fingerprint(Path.home()/'.local/bin/claude')==inspected['runtime'],
            'CAPABILITY_MISMATCH','Executable changed since inspection')
    require(not (run/'launch.json').exists(),'OWNERSHIP_CONFLICT','This fixture already launched; retain it and use a fresh run directory')
    journal=Journal(run/'state');journal.close()
    atomic_json(run/'state/compat/runtime.json',dict(schema_version=1,providers={'claude':inspected['runtime']},
        scope='Provisional disposable qualification only; daily-use approval is unchanged'))
    atomic_json(run/'state/policy.json',dict(task_wall_seconds=240,group_wall_seconds=420,followup_grace_seconds=5))
    fixture=private_directory(run/'workspace');(fixture/'fixture.txt').write_text('alpha=7\nbeta=11\ngamma=13\n')
    marker='CLAUDE-QUALIFIED-'+new_id()
    atomic_json(run/'spec.json',dict(schema_version=1,client_request_id='claude-qualification-'+new_id(),completion_mode='push',
        workers=[dict(provider='claude',model='sonnet',title='Claude runtime qualification',permission_profile='analysis',
            workspace={'mode':'inherit-read-only'},task='Read fixture.txt with native Read. Report sum=31, gamma=13 and '+marker+'. Do not delegate or write files.')],
        limits=dict(max_concurrent_workers=1,max_depth=1,wall_time_seconds=240,max_task_turns_per_worker=4,followup_grace_seconds=5)))
    settings=json.loads((Path.home()/'.claude/settings.json').read_text())
    command=f'{ROOT}/.venv/bin/python {Path(__file__).resolve()}'
    atomic_json(run/'settings.json',dict(enabledPlugins={k:False for k in settings.get('enabledPlugins',{})},
        permissions={'allow':['Bash('+command+' '+action+' --run '+str(run)+')' for action in ('parent','receipt')]}))
    atomic_json(run/'prepared.json',dict(at=stamp(),marker=marker,reviewed_sha256=reviewed))
    return dict(prepared=True,daily_approval_changed=False,model_inputs=0)


def launch(run):
    inspected=read(run,'inspect.json')
    require(fingerprint(Path.home()/'.local/bin/claude')==inspected['runtime'],
            'CAPABILITY_MISMATCH','Executable changed before launch')
    require(sources()==inspected['source'],'CAPABILITY_MISMATCH','Source changed; inspect again before a new qualification')
    require(not (run/'launch.json').exists(),'OWNERSHIP_CONFLICT','Do not replay a launched fixture')
    script=Path(__file__).resolve();prefix=f'{ROOT}/.venv/bin/python {script}'
    prompt=f'''Authorized disposable NativePeers Claude qualification. Run exactly once using native Bash:
{prefix} parent --run {run}
The helper spawns one real native Claude worker, then arms a bounded completion push. Do not answer its task or start other workers. After the helper returns say WAITING-CLAUDE-QUALIFICATION and end your turn. When the NativePeers completion arrives in this exact conversation, run exactly once:
{prefix} receipt --run {run}
Then report sum=31, gamma=13 and the CLAUDE-QUALIFIED marker from the actual worker completion. No other commands, configuration edits, permission changes or peer replies are authorized.'''
    native=new_id();binary=inspected['runtime']['path']
    argv=[binary,'--name','NativePeers Claude qualification','--session-id',native,'--model','sonnet',
          '--permission-mode','dontAsk','--settings',str(run/'settings.json'),
          '--strict-mcp-config','--mcp-config','{"mcpServers":{}}','--tools','Bash,Read','--',prompt]
    from nativepeers.process import identity
    atomic_json(run/'launch.json',dict(at=stamp(),native_id=native,owner_before_exec=identity(os.getpid()).json(),
        runtime=inspected['runtime'],argv=argv,policy='Disposable dontAsk; existing inbound policy retained'))
    environment=launch_environment('claude');environment.update(NPEER_HOME=str(run/'state'),PYTHONPATH=str(ROOT),TERM='xterm-256color')
    os.chdir(run/'workspace');os.execve(binary,argv,environment)


async def parent(run, receipt=False):
    journal=Journal(run/'state')
    try:
        core=Core(journal);caller=bind_caller(journal,core.registry,allow_external=False)
        peer=core.registry.verify(caller.key,caller.incarnation)
        require(peer['peer_key']['native_session_id']==read(run,'launch.json')['native_id'],
                'OWNERSHIP_CONFLICT','Fixture helper is outside its exact native parent')
        if receipt:
            initial=read(run,'parent.json');result=core.delegation.result(caller,initial['group_id'])
            route=result['notifications'][0];message=route.get('delivery',{}).get('message_id')
            require(message,'PROTOCOL_ERROR','Completion has no durable native message')
            observed=await core.status(caller,message,refresh=True)
            atomic_json(run/'receipt.json',dict(at=stamp(),parent_native_id=peer['peer_key']['native_session_id'],
                incarnation=caller.incarnation,outcome=observed,result=result))
            return dict(recorded=True,delivery_state=observed['state'])
        require(not (run/'parent.json').exists(),'OWNERSHIP_CONFLICT','Do not replay the parent helper')
        spawned=await core.tool(caller,'worker_spawn',{'spec':read(run,'spec.json')})
        atomic_json(run/'parent.json',dict(at=stamp(),group_id=spawned['group_id'],parent=peer,spawn=spawned))
        waited=await core.tool(caller,'task_wait',dict(handle=spawned['group_id'],timeout_ms=1,on_timeout='push'))
        atomic_json(run/'wait.json',waited)
        return dict(group_id=spawned['group_id'],wait=waited['state'] if 'state' in waited else 'RESULT_AVAILABLE')
    finally:journal.close()


def collect(run):
    receipt=read(run,'receipt.json');result=receipt['result'];prepared=read(run,'prepared.json')
    worker=result['results'][0];expected=read(run,'launch.json')['native_id']
    require(worker['status']=='completed' and 'sum=31' in worker['summary'] and prepared['marker'] in worker['summary'],
            'PROTOCOL_ERROR','Native worker finding is incomplete')
    require(receipt['outcome']['state'] in ('INGESTED','MODEL_REPORTED_PROCESSED','REPLY_RECORDED'),
            'PROTOCOL_ERROR','Native parent ingestion is not established')
    require(receipt['parent_native_id']==expected,'OWNERSHIP_CONFLICT','Completion reached another parent')
    require(worker['worker']['native_session_id']!=expected,'OWNERSHIP_CONFLICT','Worker identity equals parent')
    journal=Journal(run/'state')
    try:
        rows=journal.all("SELECT subject_id,payload_json FROM events WHERE kind='effective-tool-profile'")
        require(rows,'PROTOCOL_ERROR','No effective native worker tool inventory')
        for row in rows:
            tools=json.loads(row['payload_json'])['tools']
            require(all(t in ('Read','Glob','Grep','ToolSearch','ListMcpResourcesTool','ReadMcpResourceTool') or t.startswith('mcp__nativepeers__') for t in tools),
                    'PERMISSION_CAPABILITY_UNKNOWN','Analysis worker exposed an ungranted tool')
    finally:journal.close()
    record=dict(at=stamp(),status='PASS',evidence_class='live',runtime=read(run,'inspect.json')['runtime'],
        source=read(run,'inspect.json')['source'],parent_native_id=expected,
        worker_native_id=worker['worker']['native_session_id'],native_turn_ids=worker.get('native_turn_ids'),
        message_id=receipt['outcome']['message_id'],delivery_state=receipt['outcome']['state'],
        checks=['structured worker native ID/result','effective analysis tools','exact original-parent completion ingestion'],
        limitations=['Disposable dontAsk parent; everyday inbound policy may hold or refuse','Quota approval is unchanged'])
    atomic_json(run/'qualification.json',record);return record


def approve(run):
    proof=read(run,'qualification.json');inspected=read(run,'inspect.json')
    require(not Path(inspected['daily_state']).resolve().is_relative_to(run.resolve()),
            'APPROVAL_BLOCKED','Daily approval target cannot be inside disposable qualification state')
    require(proof['status']=='PASS' and proof['source']==sources(),'CAPABILITY_MISMATCH','Qualification source is stale')
    journal=Journal(Path(inspected['daily_state']))
    try:
        from nativepeers.install import Installer
        with Installer(journal).lock():
            # Rehash the selected link under the manager lock immediately before the
            # runtime lock's compare-and-write; preserve every other provider receipt.
            current=fingerprint(Path.home()/'.local/bin/claude')
            require(current==proof['runtime'],'CAPABILITY_MISMATCH','Claude updated after qualification; approval stopped')
            replacement=dict(current,version=inspected['version'],scope='Claude structured worker and interactive inbox/completion; '+str(run/'qualification.json'))
            update_verified(journal,{'claude':replacement},expected={'claude':inspected['expected_approval']})
        return dict(approved=True,runtime=replacement,quota_approval_changed=False)
    finally:journal.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['inspect','prepare','launch','parent','receipt','collect','approve'])
    parser.add_argument('--run',type=Path,required=True);parser.add_argument('--reviewed-sha256')
    args=parser.parse_args();run=args.run.resolve()
    require(run.is_relative_to(ROOT/'evidence/scratch'),'APPROVAL_BLOCKED','Keep disposable qualification beneath project evidence/scratch')
    private_directory(run)
    if args.action in ('parent','receipt'):value=asyncio.run(parent(run,args.action=='receipt'))
    elif args.action=='prepare':value=prepare(run,args.reviewed_sha256)
    else:value={'inspect':inspect,'launch':launch,'collect':collect,'approve':approve}[args.action](run)
    print(json.dumps(value,indent=2))


if __name__=='__main__':main()
