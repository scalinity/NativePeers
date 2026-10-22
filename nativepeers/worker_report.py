"""Optional worker-supplied structure; raw provider output is always retained."""
import json

from .protocol import valid

PROMPT = '''
If you have findings or checks to report, optionally append one fenced nativepeers-result JSON object to your final answer:
```nativepeers-result
{"findings":[{"severity":"info","summary":"brief finding","file":"optional path","line":1}],"tests_run":[{"command":"actual command","outcome":"passed","details":"actual observation"}]}
```
Use outcome passed, failed or not_run. Report only checks actually attempted; these remain worker-reported, not independently verified. Do not run an extra turn to format this object.
'''
TEXT={'type':'string','maxLength':2000}
FINDING={'type':'object','required':['severity','summary'],'additionalProperties':False,'properties':{
    'severity':{'enum':['critical','warning','info']},'summary':TEXT,'file':TEXT,'line':{'type':'integer','minimum':1}}}
CHECK={'type':'object','required':['command','outcome'],'additionalProperties':False,'properties':{
    'command':TEXT,'outcome':{'enum':['passed','failed','not_run']},'details':TEXT}}


def extract(text):
    findings=[];checks=[];limitations=[]
    marker='```nativepeers-result\n'
    if marker not in text:
        return findings,checks,['No structured worker report supplied; provider final text retained.']
    raw=text.rsplit(marker,1)[1].split('```',1)[0]
    try:
        if len(raw.encode())>16384:raise ValueError('oversize')
        value=json.loads(raw)
        if not isinstance(value,dict) or set(value)-{'findings','tests_run'}:raise ValueError('invalid report')
    except (ValueError,TypeError):
        return findings,checks,['Malformed structured worker report; provider final text retained.']
    for field,schema,output in [('findings',FINDING,findings),('tests_run',CHECK,checks)]:
        items=value.get(field,[])
        if not isinstance(items,list) or len(items)>20:
            limitations.append('Invalid '+field+' list; provider final text retained.');continue
        for item in items:
            if valid(item,schema):output.append(dict(item,source='worker-reported',independently_verified=False))
            else:limitations.append('Invalid '+field+' entry; provider final text retained.')
    return findings,checks,limitations
