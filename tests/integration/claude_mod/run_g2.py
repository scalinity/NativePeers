import sys,os,time,json
sys.path.insert(0,'.')
from pty_session import Session
K=os.path.abspath('../../../evidence/scratch/claude-lineage/g2-g3-g5-g13')
s=Session(['driver','mods/iprobe'],args=['--permission-mode','default'],label='g2',keep=K,
  env={'NP_DRIVER_SCRIPT':os.path.abspath('scripts/g2_main.json'),'NP_PROBE_LOG':K+'/probe.ndjson','NP_PROBE_HOLD_MS':'25000','NP_PROBE_TIMER_MS':'6000','NP_PROBE_START_SPAWN':'1'})
def step(name,wait=1.5):
    time.sleep(wait);t=s.snap(name);print('##',name);print('\n'.join(l for l in t.splitlines() if l.strip())[-1400:]);sys.stdout.flush()
try:
    s.wait_for(r'❯',timeout=40);step('00-start',2)
    s.type_line('go');step('01-pathA',4)
    s.type_line('/npi-open');step('02-open-command',3)
    s.type_line('also check the tests');step('03-typed-running',3)
    time.sleep(30);step('04-after-run1',1)
    s.type_line('second follow-up after completion');step('05-typed-completed',4)
    time.sleep(30);step('06-after-run2',1)
    s.key('esc');step('07-main',2)
    s.type_line('/npi-spawn');step('08-cmd-spawn',4)
    s.key('ctrl+x');s.key('tab');step('09-band-focus',1);s.key('enter');step('10-band-press',3)
    time.sleep(35);step('11-late',1)
    print('hits',len(s.hits()),sorted({h['model'] for h in s.hits()}))
finally:
    s.close();print('swept',s.swept)
