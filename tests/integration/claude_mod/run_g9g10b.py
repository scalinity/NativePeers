import sys,os,time,json,shutil,glob
sys.path.insert(0,'.')
from pty_session import Session
K=os.path.abspath('../../../evidence/scratch/claude-lineage/g9-g10-b');shutil.rmtree(K,ignore_errors=True);os.makedirs(K)
mod=K+'/iprobe-copy';shutil.copytree('mods/iprobe',mod)
script=json.load(open('scripts/g9g10.json'));open(K+'/script.json','w').write(json.dumps(script))
env={'NP_DRIVER_SCRIPT':K+'/script.json','NP_PROBE_LOG':K+'/probe.ndjson','NP_PROBE_HOLD_MS':'20000','NP_PROBE_STEPS':'2'}
def events():
    out=[]
    for f in glob.glob(K+'/probe.ndjson.*'):out+= [json.loads(l)|{'file':f[-6:]} for l in open(f)]
    return sorted(out,key=lambda x:x['at'])
def step(s,name,wait=1.5,tail=700):
    time.sleep(wait);t=s.snap(name);print('##',name);print('\n'.join(l for l in t.splitlines() if l.strip() and '────' not in l)[-tail:]);sys.stdout.flush()
s=Session(['driver',mod],args=['--permission-mode','default'],label='g9b',keep=K+'/s1',env=env)
try:
    s.wait_for(r'❯',timeout=40);time.sleep(2)
    s.type_line('go');step(s,'01-pathA',8)
    open(mod+'/hooks/register.tsx','a').write('\n// hot reload marker\n');step(s,'02-after-edit',4)
    time.sleep(40);step(s,'03-after-run',1,500)
    s.type_line('/npi-list');step(s,'04-list-before-clear',2,400)
    s.type_line('/clear');step(s,'05-clear',4,400)
    s.type_line('/npi-list');step(s,'06-list-after-clear',2,400)
    first=next(x['data']['id'] for x in events() if x['kind']=='session.start')
    shadow=next(x['data']['agentId'] for x in events() if x['kind']=='shadow')
finally:
    s.close()
script['on']['message the first'][0]['tools'][0]['input']['to']=shadow;open(K+'/script.json','w').write(json.dumps(script))
r=Session(['driver',mod],args=['--permission-mode','default','--resume',first],label='g10b',keep=K+'/s2',env=env,reuse=s)
try:
    r.wait_for(r'❯',timeout=40);time.sleep(3)
    r.type_line('/npi-list');step(r,'07-list-resumed',2,400)
    r.type_line('now message the first shadow');step(r,'08-sendmessage',6,600)
    time.sleep(45);step(r,'09-end',1,900)
finally:
    r.close()
for x in events():
    k=x['kind'];d=x['data']
    if k in('session.start','session.end','reattached-from-state','mirror.start','mirror.end','turn.complete','shadow','agent.list','session.send') or k=='turn.step':
        if k=='turn.step':d={kk:d[kk] for kk in('agentId','index','run')}
        if k=='session.start':d={kk:d.get(kk) for kk in('id','shadowsInState')}
        print(x['file'],round(x['at']/1000)%10000,k,json.dumps(d)[:260])
print('hits s1',len(s.hits()),sorted({h['model'] for h in s.hits()}),'s2',len(r.hits()),sorted({h['model'] for h in r.hits()}))
