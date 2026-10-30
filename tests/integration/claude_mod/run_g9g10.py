import sys,os,time,json,shutil
sys.path.insert(0,'.')
from pty_session import Session
K=os.path.abspath('../../../evidence/scratch/claude-lineage/g9-g10-g11');os.makedirs(K,exist_ok=True)
mod=K+'/iprobe-copy';shutil.rmtree(mod,ignore_errors=True);shutil.copytree('mods/iprobe',mod)
env={'NP_DRIVER_SCRIPT':os.path.abspath('scripts/g9g10.json'),'NP_PROBE_LOG':K+'/probe.ndjson','NP_PROBE_HOLD_MS':'20000','NP_PROBE_STEPS':'2'}
def step(s,name,wait=1.5,tail=900):
    time.sleep(wait);t=s.snap(name);print('##',name);print('\n'.join(l for l in t.splitlines() if l.strip() and '────' not in l)[-tail:]);sys.stdout.flush()
s=Session(['driver',mod],args=['--permission-mode','default'],label='g9',keep=K+'/s1',env=env)
try:
    s.wait_for(r'❯',timeout=40);time.sleep(2)
    s.type_line('go');step(s,'01-pathA',5)
    p=mod+'/hooks/register.tsx';open(p,'a').write('\n// hot reload marker\n');step(s,'02-after-edit',4)
    time.sleep(40);step(s,'03-after-run',1,1400)
    s.type_line('/cost');step(s,'04-cost',3,700)
    s.key('esc');s.type_line('/clear');step(s,'05-clear',4,900)
    first=[json.loads(l) for l in open(K+'/probe.ndjson') if json.loads(l)['kind']=='session.start'][0]['data']['id']
    print('first session id',first)
finally:
    s.close()
r=Session(['driver',mod],args=['--permission-mode','default','--resume',first],label='g10',keep=K+'/s2',env=env,reuse=s)
try:
    r.wait_for(r'❯',timeout=40);step(r,'06-resumed',3,1600)
    r.key('down');step(r,'07-list',1,500)
    r.key('esc');r.type_line('now message the first shadow');step(r,'08-sendmessage',5,900)
    time.sleep(45);step(r,'09-end',1,1500)
    print('hits s1',len(s.hits()),sorted({h['model'] for h in s.hits()}),'s2',len(r.hits()),sorted({h['model'] for h in r.hits()}))
finally:
    r.close()
