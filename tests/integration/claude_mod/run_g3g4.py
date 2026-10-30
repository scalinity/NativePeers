import sys,os,time,json
sys.path.insert(0,'.')
from pty_session import Session
K=os.path.abspath('../../../evidence/scratch/claude-lineage/g3-g4')
s=Session(['driver','mods/iprobe'],args=['--permission-mode','default'],label='g3g4',keep=K,
  env={'NP_DRIVER_SCRIPT':os.path.abspath('scripts/g4_main.json'),'NP_PROBE_LOG':K+'/probe.ndjson','NP_PROBE_HOLD_MS':'30000','NP_PROBE_STEPS':'2'})
def step(name,wait=1.5,tail=1100):
    time.sleep(wait);t=s.snap(name);print('##',name);print('\n'.join(l for l in t.splitlines() if l.strip())[-tail:]);sys.stdout.flush()
try:
    s.wait_for(r'❯',timeout=40);time.sleep(2)
    s.type_line('go');step('01-two',5)
    s.key('down');s.key('down');s.key('enter');step('02-view-A1',2)
    s.type_line('also check the tests (typed while running)');step('03-typed-running',3)
    s.key('esc');step('04-esc-in-view',4)
    s.key('esc');step('05-main',2)
    s.key('down');s.key('down');s.key('down');step('06-select-A2',1)
    for k in ('x','delete','ctrl+x'):
        pass
    s.key('x');step('07-x-on-A2',3)
    time.sleep(40);step('08-later',1)
    s.key('ctrl+x');s.key('tab');s.key('right');s.key('right');s.key('right');step('09-band-focus',1);s.key('enter');step('10-band-spawn',4)
    time.sleep(35);step('11-end',1,2000)
    print('hits',len(s.hits()),sorted({h['model'] for h in s.hits()}))
finally:
    s.close();print('swept',s.swept)
