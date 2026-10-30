import sys,os,time,json,re
sys.path.insert(0,'.')
from pty_session import Session
from concurrent.futures import ThreadPoolExecutor
def one(combo):
    parent,shadow=combo
    K=os.path.abspath(f'../../../evidence/scratch/claude-lineage/g6-{parent}-{shadow}')
    env={'NP_DRIVER_SCRIPT':os.path.abspath('scripts/g6.json'),'NP_PROBE_LOG':K+'/probe.ndjson','NP_PROBE_HOLD_MS':'3000','NP_PROBE_STEPS':'3','NP_PROBE_START_SPAWN':'1'}
    if shadow!='inherit':env['NP_PROBE_PERMISSION']=shadow
    s=Session(['driver','mods/iprobe'],args=['--permission-mode',parent],label=f'g6-{parent}-{shadow}',keep=K,env=env)
    try:
        s.wait_for(r'❯',timeout=40);time.sleep(16);a=s.snap('01-run')
        if parent=='default' and shadow=='bypassPermissions':
            s.type_line('now message the first shadow');time.sleep(20);s.snap('02-sendmessage')
        log=[json.loads(l) for l in open(K+'/probe.ndjson')] if os.path.exists(K+'/probe.ndjson') else []
        starts=sum(1 for x in log if x['kind']=='mirror.start');ends=sum(1 for x in log if x['kind']=='mirror.end')
        comp=[(x['data']['reason'],x['data']['status']) for x in log if x['kind']=='turn.complete']
        perm=[x['data']['permissionMode'] for x in log if x['kind']=='agent.spawn'][:1]
        prompt=bool(re.search(r'Do you want|Allow|permission',a))
        return combo,{'mirror_start':starts,'mirror_end':ends,'complete':comp,'spawn_permissionMode_seen':perm,'prompt_on_screen':prompt,'model_hits':len(s.model_hits()),'models':sorted({h['model'] for h in s.hits()})}
    finally:
        s.close()
        dbg=open(K+'/debug.log').read() if os.path.exists(K+'/debug.log') else ''
        print('   sources',combo,sorted(set(re.findall(r'API REQUEST\] /v1/messages source=(\S+)',dbg))),flush=True)
combos=[(p,sh) for p in ('default','acceptEdits','plan','auto') for sh in ('inherit','bypassPermissions','dontAsk')]
with ThreadPoolExecutor(4) as ex:
    for c,o in ex.map(one,combos):print(c,json.dumps(o),flush=True)
