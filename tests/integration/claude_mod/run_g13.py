import sys,os,time,json
sys.path.insert(0,'.')
from pty_session import Session
K=os.path.abspath('../../../evidence/scratch/claude-lineage/g13')
os.makedirs(K,exist_ok=True)
s=Session(['driver','mods/iprobe'],args=['--permission-mode','default'],label='g13',keep=K,
  env={'NP_DRIVER_SCRIPT':K+'/script.json','NP_PROBE_LOG':K+'/probe.ndjson','NP_PROBE_HOLD_MS':'12000','NP_PROBE_STEPS':'2'})
script=json.load(open('scripts/g13.json'))
def write_script(first=''):
    t=json.dumps(script).replace('FIRSTID',first);open(K+'/script.json','w').write(t)
write_script()
def step(name,wait=1.5,tail=700):
    time.sleep(wait);t=s.snap(name);print('##',name);print('\n'.join(l for l in t.splitlines() if l.strip())[-tail:]);sys.stdout.flush()
def first_id():
    for l in open(K+'/probe.ndjson'):
        x=json.loads(l)
        if x['kind']=='shadow':return x['data']['agentId']
try:
    s.wait_for(r'❯',timeout=40);time.sleep(2)
    s.type_line('go');step('01-two',3)
    s.type_line('please stop the newest shadow');step('02-taskstop-running',5)
    time.sleep(30);step('03-B1-done',1)
    write_script(first_id())
    s.type_line('now message the first shadow');step('04-sendmessage',4)
    time.sleep(35);step('05-after-resume',1,1500)
    s.key('ctrl+x');s.key('tab');s.key('h');step('06-hook-spawn',4)
    time.sleep(35);step('07-end',1,1800)
    print('hits',len(s.hits()),sorted({h['model'] for h in s.hits()}))
finally:
    s.close();print('swept',s.swept)
