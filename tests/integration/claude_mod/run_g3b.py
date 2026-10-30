import sys,os,time,json
sys.path.insert(0,'.')
from pty_session import Session
K=os.path.abspath('../../../evidence/scratch/claude-lineage/g3b')
s=Session(['driver','mods/iprobe'],args=['--permission-mode','default'],label='g3b',keep=K,
  env={'NP_DRIVER_SCRIPT':os.path.abspath('scripts/g3b.json'),'NP_PROBE_LOG':K+'/probe.ndjson','NP_PROBE_HOLD_MS':'15000','NP_PROBE_STEPS':'2'})
def step(name,wait=1.5,tail=900):
    time.sleep(wait);t=s.snap(name);print('##',name);print('\n'.join(l for l in t.splitlines() if l.strip())[-tail:]);sys.stdout.flush()
try:
    s.wait_for(r'❯',timeout=40);time.sleep(2)
    s.type_line('go');step('01-two',4)
    s.key('down');s.key('down');s.key('enter');step('02-view-A1',2)
    s.type_line('typed while A1 runs');step('03-typed-running',2)
    time.sleep(40);step('04-A1-after',1,1600)
    s.type_line('typed after A1 completed');step('05-typed-completed',3)
    time.sleep(40);step('06-A1-run2-done',1,1600)
    s.key('ctrl+x');s.key('tab');s.key('m');step('07-main-press',2)
    s.key('esc');s.type_line('please stop the second shadow');step('08-taskstop',6)
    s.key('ctrl+x');s.key('tab');s.key('s');step('09-band-spawn',4)
    time.sleep(40);step('10-end',1,2200)
    print('hits',len(s.hits()),sorted({h['model'] for h in s.hits()}))
finally:
    s.close();print('swept',s.swept)
