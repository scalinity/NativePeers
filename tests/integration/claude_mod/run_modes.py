import json,harness,os,sys
from concurrent.futures import ThreadPoolExecutor
modes=sys.argv[1:]
def one(spec):
    mode,hold=(spec.split(':')+['500'])[:2]
    K=os.path.abspath(f'../../../evidence/scratch/claude-lineage/x-{mode}-{hold}');os.makedirs(K,exist_ok=True)
    r=harness.run('go',['driver','mods/xspike'],env={'NP_DRIVER_SCRIPT':os.path.abspath('scripts/x_mode.json'),'NP_SPIKE_LOG':K+'/spike.ndjson','NP_SPIKE_MODE':mode,'NP_SPIKE_HOLD_MS':hold},label=f'x-{mode}-{hold}',keep=K,timeout=420)
    r.pop('events');log=[json.loads(l) for l in open(K+'/spike.ndjson')] if os.path.exists(K+'/spike.ndjson') else []
    pick=[{x['kind']:x['data']} for x in log if x['kind'] in('catch','turn.complete','signal','mirror_exec.end')]
    return {'mode':spec,'zero_api':r['zero_api'],'hits':r['tripwire_hits'],'elapsed':r['elapsed_s'],'result':(r['result'] or {}).get('result'),'log':pick}
with ThreadPoolExecutor(8) as ex:
    for out in ex.map(one,modes):print(json.dumps(out)[:900])
