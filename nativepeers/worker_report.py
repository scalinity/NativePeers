"""Optional worker-supplied structure; raw provider output is always retained."""
import json
import re

from .protocol import valid

PROMPT = '''
If you have findings or checks to report, optionally append one fenced nativepeers-result JSON object to your final answer:
```nativepeers-result
{"findings":[{"severity":"info","summary":"brief finding","file":"optional path","line":1}],"tests_run":[{"command":"actual command","outcome":"passed","details":"actual observation"}]}
```
Use severity critical (Critical), warning (Warning), or info (Suggestion), never high/medium/low. Keep positive observations outside findings. Use outcome passed, failed or not_run; reading a test is not running it. These remain worker-reported, not independently verified. For a review, follow the selected NativePeers review contract and use the same F001-style IDs in readable items and summary ("F001: title"). Do not run an extra turn to format this object.
'''
TEXT={'type':'string','maxLength':2000}
FINDING={'type':'object','required':['severity','summary'],'additionalProperties':False,'properties':{
    'severity':{'enum':['critical','warning','info']},'summary':TEXT,'file':TEXT,'line':{'type':'integer','minimum':1}}}
CHECK={'type':'object','required':['command','outcome'],'additionalProperties':False,'properties':{
    'command':TEXT,'outcome':{'enum':['passed','failed','not_run']},'details':TEXT}}


def review_correspondence(text,findings):
    """Check the opted-in readable review contract, without inventing findings."""
    if not text.startswith('## Code Review:'):return []
    headings={'Critical Issues (Must Fix)':'critical','Warnings (Should Fix)':'warning','Suggestions (Consider)':'info'}
    readable=[];seen=set();severity=None;invalid=False
    for line in text.splitlines():
        if line.startswith('### '):
            severity=next((value for title,value in headings.items() if line.endswith(title)),None)
            if severity:seen.add(severity)
        elif severity and re.match(r'^\d+\. ',line):
            found=re.match(r'^\d+\. \*\*\[(F\d{3,})\] ',line)
            if found:readable.append((found[1],severity))
            else:invalid=True
    structured=[]
    for item in findings:
        found=re.match(r'^(F\d{3,}):\s+\S',item['summary'])
        if found:structured.append((found[1],item['severity']))
        else:invalid=True
    if (invalid or len(seen)!=3 or sorted(readable)!=sorted(structured)
            or len({item[0] for item in readable})!=len(readable)
            or len({item[0] for item in structured})!=len(structured)):
        return ['Review correspondence incomplete; reconcile readable IDs/categories against structured findings locally; provider final text retained.']
    return []


def extract(text):
    findings=[];checks=[];limitations=[]
    marker='```nativepeers-result\n'
    if marker not in text:
        return findings,checks,['No structured worker report supplied; provider final text retained.']
    parts=text.split(marker)
    raw=parts[-1].split('```',1)[0]
    try:
        if len(parts)!=2 or '```' not in parts[-1]:raise ValueError('ambiguous or unclosed report')
        if len(raw.encode())>16384:raise ValueError('oversize')
        value=json.loads(raw)
        if not isinstance(value,dict) or set(value)-{'findings','tests_run'}:raise ValueError('invalid report')
    except (ValueError,TypeError,RecursionError):
        return findings,checks,['Malformed structured worker report; provider final text retained.']
    for field,schema,output in [('findings',FINDING,findings),('tests_run',CHECK,checks)]:
        items=value.get(field)
        if not isinstance(items,list) or len(items)>20:
            limitations.append('Invalid '+field+' list; provider final text retained.');continue
        for item in items:
            if valid(item,schema):output.append(dict(item,source='worker-reported',independently_verified=False))
            else:limitations.append('Invalid '+field+' entry; provider final text retained.')
    limitations.extend(review_correspondence(parts[0].strip(),findings))
    return findings,checks,limitations
