"""Optional worker-supplied structure; raw provider output is always retained."""
import json
import re
from collections import Counter

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


REPORT_LIMITATION_PREFIXES=('No structured worker','Malformed structured worker','Invalid findings','Invalid tests_run','Review correspondence incomplete')


def is_review(text,findings=()):
    return bool(re.search(r'(?mi)^#{1,3} Code Review\b',text) or any(re.match(r'^F\d{3,}:\s',item['summary']) for item in findings))


def report_incomplete(text,limitations):
    # An absent optional appendix is ordinary for non-review implementation work.
    supplied='```nativepeers-result' in text or is_review(text)
    return supplied and any(str(item).startswith(REPORT_LIMITATION_PREFIXES) for item in limitations)


def review_correspondence(text,findings):
    """Check the opted-in readable review contract, without inventing findings."""
    header=re.search(r'(?m)^## Code Review:',text)
    if not header and not is_review(text,findings):return []
    headings={'Critical Issues (Must Fix)':'critical','Warnings (Should Fix)':'warning','Suggestions (Consider)':'info'}
    readable=[];seen=set();severity=None;invalid=not bool(header);fence=None
    for line in (text[header.start():] if header else text).splitlines():
        marker=re.match(r'^\s*(`{3,}|~{3,})',line)
        if marker:
            value=marker[1]
            if fence is None:fence=value
            elif value[0]==fence[0] and len(value)>=len(fence):fence=None
            continue
        if fence:continue
        if line.startswith('### '):
            severity=next((value for title,value in headings.items() if line.endswith(title)),None)
            if severity:seen.add(severity)
        elif severity and re.match(r'^\d+\. ',line):
            found=re.match(r'^\d+\. \*\*\[(F\d{3,})\] ',line)
            location=line.rsplit(' - ',1)[-1].strip('` ')
            path,separator,number=location.rpartition(':')
            if found and separator and path and number.isdecimal() and int(number)>0:
                readable.append((found[1],severity,path,int(number)))
            else:invalid=True
    structured=[]
    for item in findings:
        found=re.match(r'^(F\d{3,}):\s+\S',item['summary'])
        if found:structured.append((found[1],item['severity'],item.get('file'),item.get('line')))
        else:invalid=True
    if (invalid or len(seen)!=3 or Counter(readable)!=Counter(structured)
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
        items=value.get(field,None if is_review(parts[0]) else [])
        if not isinstance(items,list) or len(items)>20:
            limitations.append('Invalid '+field+' list; provider final text retained.');continue
        for item in items:
            if valid(item,schema):output.append(dict(item,source='worker-reported',independently_verified=False))
            else:limitations.append('Invalid '+field+' entry; provider final text retained.')
    limitations.extend(review_correspondence(parts[0].strip(),findings))
    return findings,checks,limitations
