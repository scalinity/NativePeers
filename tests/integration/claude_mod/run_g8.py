import json,harness,os,re,sys
from concurrent.futures import ThreadPoolExecutor
CASES={
 'g8-tool-20min':dict(mode='normal',hold='1200000',script='x_mode.json',timeout=1800),
 'g8-silent-30min':dict(mode='silent:1800000',hold='500',script='x_mode.json',timeout=2400),
 'g8-steps600-default':dict(mode='steps:600',hold='0',script='x_mode.json',timeout=900),
 'g8-steps600-max1000':dict(mode='steps:600',hold='0',script='x_mode.json',timeout=900,maxturns='1000'),
 'g8-concurrent6':dict(mode='normal',hold='60000',script='x_concurrent6.json',timeout=600)}
def one(name):
    c=CASES[name];K=os.path.abspath(f'../../../evidence/scratch/claude-lineage/{name}');os.makedirs(K,exist_ok=True)
    env={'NP_DRIVER_SCRIPT':os.path.abspath('scripts/'+c['script']),'NP_SPIKE_LOG':K+'/spike.ndjson','NP_SPIKE_MODE':c['mode'],'NP_SPIKE_HOLD_MS':c['hold'],'NP_SPIKE_WAIT_MS':'3000000'}
    if c.get('maxturns'):env['NP_SPIKE_MAXTURNS']=c['maxturns']
    r=harness.run('go',['driver','mods/xspike'],env=env,label=name,keep=K,timeout=c['timeout'],prefix=('/usr/bin/time','-l'))
    r.pop('events');log=[json.loads(l) for l in open(K+'/spike.ndjson')] if os.path.exists(K+'/spike.ndjson') else []
    rss=re.search(r'(\d+)\s+maximum resident set size',open(K+'/stderr.txt').read())
    steps=[x['data'] for x in log if x['kind']=='turn.step']
    out={'case':name,'zero_api':r['zero_api'],'hits':r['tripwire_hits'],'elapsed_s':r['elapsed_s'],'exit':r['exit_code'],'max_rss_mb':round(int(rss.group(1))/2**20,1) if rss else None,
         'shadow_steps':len(steps),'max_index':max([s['index'] for s in steps],default=None),
         'completes':[{k:x['data'][k] for k in('agentId','reason','isAborted')} for x in log if x['kind']=='turn.complete'],
         'mirror_ms':[x['data']['ms'] for x in log if x['kind']=='mirror_exec.end'][:8],'signals':[x['data'] for x in log if x['kind']=='signal'][:8]}
    open(K+'/summary.json','w').write(json.dumps(out,indent=1));return out
with ThreadPoolExecutor(5) as ex:
    for o in ex.map(one,sys.argv[1:] or list(CASES)):print(json.dumps(o)[:1500],flush=True)
