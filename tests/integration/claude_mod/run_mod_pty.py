import sys,os,time,json
sys.path.insert(0,'.')
from pty_session import Session
HERE=os.path.dirname(os.path.abspath(__file__));MOD=os.path.abspath(os.path.join(HERE,'..','..','..','integrations','claude','mod','np'))
K=os.path.abspath('../../../evidence/scratch/claude-lineage/mod-pty')
opts={'npeerPath':os.path.join(HERE,'bin','npeer-replay')}
s=Session(['driver',MOD],args=['--permission-mode','default'],label='mod-pty',keep=K,settings={'pluginConfigs':{'np':{'options':opts},'np@inline':{'options':opts}}},
  env={'NP_DRIVER_SCRIPT':os.path.abspath('scripts/m3_pathA.json'),'NP_REPLAY_SCENARIO':os.path.abspath('scenarios/codex_slow.json'),'NP_REPLAY_LOG':K+'/replay.ndjson'})
def step(name,wait=1.5,tail=2200):
    time.sleep(wait);t=s.snap(name);print('##',name);print('\n'.join(l for l in t.splitlines() if l.strip() and '────' not in l)[-tail:]);sys.stdout.flush()
try:
    s.wait_for(r'❯',timeout=40);step('00-start',3,800)
    s.type_line('go');step('01-spawned',5)
    s.key('down');s.key('down');s.key('enter');step('02-view',5)
    time.sleep(12);step('03-view-tools',1)
    time.sleep(25);step('04-view-done',1)
    s.key('ctrl+x');s.key('tab');s.key('m');step('05-main',3,1600)
    print('hits',len(s.hits()),sorted({h['model'] for h in s.hits()}))
finally:
    s.close();print('swept',s.swept)
