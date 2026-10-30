"""Zero-API harness for the production np mod over the replay backend."""
import json,os,sys
import harness
HERE=os.path.dirname(os.path.abspath(__file__))
MOD=os.path.abspath(os.path.join(HERE,'..','..','..','integrations','claude','mod','np'))
def run(label,scenario,script,*,timeout=180,extra_env=None,args=()):
    K=os.path.abspath(os.path.join(HERE,'..','..','..','evidence','scratch','claude-lineage','mod-'+label));os.makedirs(K,exist_ok=True)
    for f in ('replay.ndjson',):
        if os.path.exists(os.path.join(K,f)):os.remove(os.path.join(K,f))
    opts={'npeerPath':os.path.join(HERE,'bin','npeer-replay')}
    settings=json.dumps({'pluginConfigs':{'np':{'options':opts},'np@inline':{'options':opts}}})
    env={'NP_DRIVER_SCRIPT':os.path.join(HERE,'scripts',script),'NP_REPLAY_SCENARIO':os.path.join(HERE,'scenarios',scenario),'NP_REPLAY_LOG':K+'/replay.ndjson',**(extra_env or {})}
    r=harness.run('go',[os.path.join(HERE,'driver'),MOD],env=env,args=['--settings',settings,*args],label='mod-'+label,keep=K,timeout=timeout)
    replay=[json.loads(l) for l in open(K+'/replay.ndjson')] if os.path.exists(K+'/replay.ndjson') else []
    return r,replay,K
def shadow_rows(events):
    out=[]
    for e in events:
        if e.get('parent_tool_use_id') and e['type'] in('assistant','user'):
            for c in e['message']['content']:
                out.append((e['type'],c.get('type'),c.get('name') or '',str(c.get('text') or c.get('content') or c.get('input') or '')[:300]))
    return out
if __name__=='__main__':
    r,replay,K=run(sys.argv[1],sys.argv[2],sys.argv[3])
    ev=r.pop('events')
    print(json.dumps({k:r[k] for k in ('zero_api','tripwire_hits','checks','elapsed_s','exit_code')}))
    print('replay:',[x['kind'] for x in replay])
    for row in shadow_rows(ev):print('  shadow',row)
    main=[(e['type'],str(e['message']['content'])[:160]) for e in ev if e['type'] in('user','assistant') and not e.get('parent_tool_use_id')]
    for m in main:print('  main',m)
