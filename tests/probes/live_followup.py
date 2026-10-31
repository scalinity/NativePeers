#!/usr/bin/env python3
"""One installed owned Codex fixture: retained/push/quiet follow-up phases.

Uses the qualified standalone TUI and localhost Responses harness. No external
inference, daily provider config, approval, live session or receipt is changed.
Run only after reviewed source is integrated into the installed project.
"""
from contextlib import closing
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import threading
import time
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parent))
import quiet_close_worker as q
base=q.base


def until(predicate,seconds=90,pump=lambda:None):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        pump()
        if predicate():return
        time.sleep(.03)
    raise TimeoutError('Owned follow-up fixture deadline')


def helper(config_path,token):
    config=json.loads(Path(config_path).read_text());run=Path(config['run'])
    if Path(os.environ['NPEER_HOME']).resolve()!=run/'peer-state':raise RuntimeError('Nonfixture journal')
    call=lambda *args:base.product_cli(config,*args)
    try:
        who=call('whoami');base.save(run/'identity.json',who);call('notifications','open')
        spawned=call('spawn','--spec',str(run/'spawn.json'));base.save(run/'spawned.json',spawned)
        worker=spawned['workers'][0]['worker_id'];group=spawned['group_id'];first=spawned['tasks'][0]['task_id']
        def terminal(task):
            return call('wait',task,'--timeout-ms','1000','--on-timeout','retain')['ready']
        until(lambda:terminal(first))
        status=call('status',group);target=next(w['peer_key'] for w in status['workers'] if w['worker_id']==worker)
        (run/'refused-task.txt').write_text('Do not execute: the live writer must refuse resume.')
        refused=subprocess.run([sys.executable,'-m','nativepeers','resume-worker',worker,'--task-file',str(run/'refused-task.txt'),'--client-request-id',str(uuid.uuid4()),'--json'],capture_output=True,text=True,timeout=30)
        refusal=json.loads(refused.stdout);base.save(run/'live-writer-refusal.json',refusal)
        if refusal['ok'] or refusal['error']['code']!='OWNERSHIP_CONFLICT' or target not in refusal['error']['details']['command']:
            raise RuntimeError('Live writer refusal lacks exact serialized route')
        def send(phase,requested):
            value=call('send','--to',target,'--message',f'NPF_TASK_{phase}_{token}','--client-request-id',f'{token}-{phase}',*(['--request-reply'] if requested else []))
            base.save(run/f'sent-{phase}.json',value)
            link=value['followup']
            if link['group_id']!=group or link['completion_phase']!=phase or link['completion_mode']!=('push' if requested else 'wait'):
                raise RuntimeError('Incorrect admitted phase/mode linkage')
            return value
        initial_route=status['notifications'][0]
        retained=send(2,False);until(lambda:terminal(retained['followup']['task_id']))
        after=call('status',group);base.save(run/'after-wait.json',after)
        if after['notifications'][0]!=initial_route or after['notifications'][1]['state']!='NOTIFICATION_UNKNOWN':
            raise RuntimeError('Task-specific wait altered another phase or did not claim its own')
        pushed=send(3,True)
        base.save(run/'ready.json',{'ok':True});print('NPF_READY',flush=True)
        q.wait_file(run/'quiet')
        closed=call('notifications','close');base.save(run/'closed.json',closed)
        if closed['state']!='QUIET':raise RuntimeError('Fixture did not close')
        quiet=send(4,True)
        until(lambda:call('status',quiet['message_id'])['followup']['task_state']=='COMPLETED')
        until(lambda:call('status',group)['notifications'][-1]['state']=='RETAINED')
        final=call('result',group);retained_messages=call('inbox','--retained','--limit','100')
        status3=call('status',pushed['message_id']);status4=call('status',quiet['message_id'])
        completion=call('status',final['notifications'][2]['delivery']['message_id'],'--refresh')
        if completion['state']!='INGESTED':raise RuntimeError('Push lacks exact recipient ingestion evidence')
        if status3['followup']['task_state']!='COMPLETED' or status4['followup']['task_state']!='COMPLETED':
            raise RuntimeError('Message status lacks terminal task linkage')
        call('cancel',group,'--reason','Owned fixture complete')
        base.save(run/'collected.json',dict(result=final,retained=retained_messages,status3=status3,status4=status4,completion=completion))
        q.wait_file(run/'exit')
    except BaseException as error:
        base.save(run/'helper-error.json',dict(error=repr(error)));base.save(run/'ready.json',dict(ok=False,error=repr(error)));raise


class Model(q.Model):
    def answer(self,payload):
        latest=base.last_user_text(payload)
        match=re.search(r'NPF_TASK_([1-4])_'+self.token,latest)
        if match and 'NativePeers assigned worker title:' in latest:
            phase=int(match[1])
            if payload.get('model')!=q.MODEL or payload.get('reasoning',{}).get('effort')!=q.EFFORT:raise RuntimeError('Worker model/effort differs')
            (self.run/f'entered-{phase}').touch();q.wait_file(self.run/f'release-{phase}')
            return dict(type='message',role='assistant',content=[dict(type='output_text',text=f'NPF_RESULT_{phase}_{self.token}')])
        if f'NPF_RESULT_3_{self.token}' in latest and 'NativePeers · Worker results' in latest:
            self.parent_requests+=1
            return dict(type='message',role='assistant',content=[dict(type='output_text',text='NPF_PUSH_RECEIVED_'+self.token)])
        return super().answer(payload)


def run_fixture(source,run,binary,approval,resources,report):
    import pyte
    from nativepeers.journal import Journal
    token=uuid.uuid4().hex[:12];model=Model(run);model.token=token;model.phase='followup';model.parent_requests=0
    resources.append(model);model.thread=threading.Thread(target=model.serve_forever,daemon=True);model.thread.start()
    cwd,env,state,rpc=q.prepare(source,run,binary,approval,model,resources,report)
    base.save(state/'policy.json',{'followup_grace_seconds':60})
    spec=q.spawn_spec('open',token);spec.update(completion_mode='wait');spec['limits'].update(max_task_turns_per_worker=4,followup_grace_seconds=60)
    spec['workers'][0]['task']='NPF_TASK_1_'+token;base.save(run/'spawn.json',spec)
    config=dict(run=str(run),source=str(source),binary=str(binary),cwd=str(cwd));base.save(run/'config.json',config)
    boot,final='NPF_BOOT_'+token,'NPF_PARENT_FINAL_'+token
    model.plan(boot,shlex.join([sys.executable,str(Path(__file__).resolve()),'_helper',str(run/'config.json'),token]),run/'ready.json',final)
    tui=base.Tui([str(binary),'--no-daemon','--model','fixture','--name','NativePeers followup '+token,boot],cwd,env,run,'parent',pyte)
    tracker=q.Detached(state);resources.extend([tui,tracker])
    def pump():
        tracker.capture();tui.pump()
        if model.errors or (run/'helper-error.json').exists() or tui.process.poll() is not None:raise RuntimeError('Owned fixture failed; preserve evidence')
    for phase in (1,2):
        until(lambda:(run/f'entered-{phase}').exists(),pump=pump);(run/f'release-{phase}').touch()
    until(lambda:(run/'entered-3').exists() and final in tui.text(),pump=pump)
    who=json.loads((run/'identity.json').read_text())
    if who['owner']['pid']!=tui.process.pid or who['owner']['start']!=tui.birth['start']:raise RuntimeError('Parent native birth differs')
    (run/'release-3').touch();answer='NPF_PUSH_RECEIVED_'+token
    until(lambda:answer in tui.text() and base.native_tail(run/'codex-home',who['native_session_id'],answer) is not None,pump=pump)
    before=model.parent_requests;(run/'quiet').touch()
    until(lambda:(run/'entered-4').exists(),pump=pump);(run/'release-4').touch()
    until(lambda:(run/'collected.json').exists(),pump=pump)
    data=json.loads((run/'collected.json').read_text());result=data['result'];group=result['group_id']
    if [r['completion_phase'] for r in result['results']]!=[1,2,3,4] or [n['state'] for n in result['notifications']]!=['UNCLAIMED','NOTIFICATION_UNKNOWN','PUSH_ACCEPTED','RETAINED']:
        raise RuntimeError('Phase attribution or delivery dispositions differ')
    if any(r['status']!='completed' or r['model_requested']!=q.MODEL or r['model_observed']!=q.MODEL or r['summary']!=f'NPF_RESULT_{n}_{token}' for n,r in enumerate(result['results'],1)):
        raise RuntimeError('Native task identity/model/result differs')
    native_ids={r['worker']['native_session_id'] for r in result['results']}
    if len(native_ids)!=1:raise RuntimeError('Follow-ups did not use the same owned native history')
    with closing(Journal(state,read_only=True)) as journal:
        messages=journal.all("SELECT m.* FROM messages m JOIN send_requests s USING(message_id) JOIN completion_routes c ON c.delivery_id=s.client_request_id WHERE c.group_id=?",(group,))
        if len(messages)!=2:raise RuntimeError('Duplicate or missing phase notices')
        stages={json.loads(m['payload_json'])['body']['text']:journal.all('SELECT stage FROM receipts WHERE message_id=?',(m['message_id'],)) for m in messages}
    if not any('NPF_RESULT_4_'+token in str(m) for m in data['retained']):raise RuntimeError('Quiet result was not readable')
    end=time.monotonic()+5;until(lambda:time.monotonic()>=end,pump=pump)
    if model.parent_requests!=before:raise RuntimeError('Quiet follow-up caused parent model work')
    base.require_native_silence(run/'codex-home',who['native_session_id'],answer,['NPF_RESULT_4_'+token])
    report.update(identity=who,group_id=group,native_worker_id=next(iter(native_ids)),worker_id=result['workers'][0]['worker_id'],tasks=result['tasks'],notifications=result['notifications'],receipt_stages=stages,parent_requests=model.parent_requests,results=data,external_model_inputs='NOT_INSTRUMENTED; localhost fixture configured')
    until(lambda:json.loads((state/'groups'/group/'owner.json').read_text()).get('released') is True,pump=pump)


def main():
    if len(sys.argv)>1 and sys.argv[1]=='_helper':helper(*sys.argv[2:]);return 0
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--nativepeers-root',required=True);parser.add_argument('--run',required=True);args=parser.parse_args()
    source=Path(args.nativepeers_root).resolve(strict=True);sys.path.insert(0,str(source))
    if source!=Path('/Users/danny/Documents/Tools/NativePeers'):raise base.Blocked('Probe requires the reviewed installed source; no candidate activation')
    run=base.prepare_run(args.run,source);resources=[];report=dict(status='RUNNING',started_at=base.eastern(),probe_sha256=base.digest(__file__))
    try:
        from nativepeers.compatibility import fingerprint
        binary,selection=base.select_codex(None);actual=fingerprint(binary,fresh=True);receipt=Path.home()/'.local/state/nativepeers/compat/runtime.json';prior=base.digest(receipt)
        approval=q.approved_record(receipt,actual);report.update(executable=actual,selection=selection)
        source_hashes={str(p.relative_to(source)):base.digest(p) for p in (source/'nativepeers').rglob('*.py')}
        run_fixture(source,run,binary,approval,resources,report)
        if base.digest(receipt)!=prior or fingerprint(binary,fresh=True)!=actual or any(base.digest(source/name)!=value for name,value in source_hashes.items()):raise RuntimeError('Installed source/runtime/approval drift')
        report['status']='PASS'
    except BaseException as error:report.update(status='BLOCKED' if isinstance(error,(base.Blocked,ImportError)) else 'FAIL',error=repr(error))
    finally:
        for phase in range(1,5):(run/f'release-{phase}').touch()
        (run/'exit').touch();q.stop_resources(resources,report);report['finished_at']=base.eastern();base.save(run/'qualification.json',report)
    print(json.dumps(dict(status=report['status'],report=str(run/'qualification.json'))));return 0 if report['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
