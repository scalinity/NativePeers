"""Config-only AppServer fixture. No model, hook or tool execution is supported."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tomllib

import tomlkit


home=Path(os.environ.get('CODEX_HOME',Path.home()/'.codex'))
config=home/'config.toml'


def version():
    return hashlib.sha256(config.read_bytes()).hexdigest()


for line in sys.stdin:
    request=json.loads(line)
    with (home/'fake-app-server.jsonl').open('a') as stream:
        stream.write(json.dumps(dict(request,codex_home=str(home)))+'\n')
    if 'id' not in request:continue
    method=request['method'];params=request.get('params',{})
    if method=='initialize':result={}
    elif method=='config/read':
        mode=tomllib.loads(config.read_text()).get('fixture_layer','user')
        name={'type':'user','file':str(config)}
        if mode=='other':name['file']=str(home/'other.toml')
        if mode=='profile':name['profile']='fixture'
        result={'layers':[] if mode=='missing' else [{'name':name,'version':version()}]}
    elif method=='hooks/list':
        data=json.loads((home/'hooks.json').read_text())
        hooks=[dict(handler,sourcePath=str(home/'hooks.json'),key=event,currentHash='hash-'+event)
               for event,groups in data.get('hooks',{}).items() for group in groups for handler in group.get('hooks',[])]
        result={'data':[{'hooks':hooks}]}
    elif method=='config/batchWrite':
        assert params['filePath']==str(config)
        assert params['expectedVersion']==version()
        assert params['reloadUserConfig'] is False
        assert params['edits'][0]['keyPath']=='hooks.state'
        document=tomlkit.parse(config.read_text())
        document.setdefault('hooks',{})['state']=params['edits'][0]['value']
        config.write_text(tomlkit.dumps(document))
        result={}
    else:
        raise AssertionError('Unexpected provider method: '+method)
    print(json.dumps({'id':request['id'],'result':result}),flush=True)
