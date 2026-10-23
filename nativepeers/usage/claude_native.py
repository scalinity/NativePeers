"""Native usage text, preserving separate rows, resets and cached-source markers."""
from datetime import datetime
import re
import time
import unicodedata

from ..errors import PeerError,require
from .normalize import pool,timestamp


class Screen:
    """Bounded VT screen for the collector's own terminal; never an active TUI."""
    def __init__(self,rows=200,columns=160):
        self.rows=rows;self.columns=columns;self.cells=[[' ']*columns for _ in range(rows)]
        self.row=0;self.col=0;self.pending=''

    def feed(self,text):
        text=self.pending+text;self.pending='';i=0
        while i<len(text):
            c=text[i]
            if c=='\x1b':
                match=re.match(r'\x1b\[([0-?]*)([ -/]*)([@-~])',text[i:])
                if match:
                    raw,_,cmd=match.groups()
                    if cmd not in 'HfABCDGJK':i+=match.end();continue
                    values=[int(x) if x else 0 for x in raw.lstrip('?').split(';')]
                    n=values[0] or 1
                    if cmd in ('H','f'):self.row=min(self.rows-1,max(0,n-1));self.col=min(self.columns-1,max(0,(values[1] if len(values)>1 else 1)-1))
                    elif cmd=='A':self.row=max(0,self.row-n)
                    elif cmd=='B':self.row=min(self.rows-1,self.row+n)
                    elif cmd=='C':self.col=min(self.columns-1,self.col+n)
                    elif cmd=='D':self.col=max(0,self.col-n)
                    elif cmd=='G':self.col=min(self.columns-1,max(0,n-1))
                    elif cmd=='J':
                        if values[0] in (2,3):self.cells=[[' ']*self.columns for _ in range(self.rows)]
                        else:
                            self.cells[self.row][self.col:]=[' ']*(self.columns-self.col)
                            for r in range(self.row+1,self.rows):self.cells[r]=[' ']*self.columns
                    elif cmd=='K':
                        a,b=(0,self.columns) if values[0]==2 else (0,self.col+1) if values[0]==1 else (self.col,self.columns)
                        self.cells[self.row][a:b]=[' ']*(b-a)
                    i+=match.end();continue
                if text[i:].startswith('\x1b]'):
                    end=re.search(r'\x07|\x1b\\',text[i:])
                    if not end:self.pending=text[i:];break
                    i+=end.end();continue
                if text[i:].startswith('\x1b[') or len(text)-i==1:self.pending=text[i:];break
                i+=2;continue
            if c=='\r':self.col=0
            elif c=='\n':
                self.row+=1
                if self.row>=self.rows:self.cells.pop(0);self.cells.append([' ']*self.columns);self.row=self.rows-1
            elif c=='\b':self.col=max(0,self.col-1)
            elif c.isprintable() and not unicodedata.combining(c):
                if self.col>=self.columns:self.col=0;self.row=min(self.rows-1,self.row+1)
                self.cells[self.row][self.col]=c;self.col+=2 if unicodedata.east_asian_width(c) in ('W','F') else 1
            i+=1

    def text(self):return '\n'.join(''.join(row).rstrip() for row in self.cells).strip()


def parse(text,*,fresh_transport=False,now=None):
    now=time.time() if now is None else now
    text=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',text).replace('\r','')
    folded=' '.join(text.lower().split())
    cached=any(s in folded for s in ('last-known','last known','could not refresh','usage endpoint is rate limited','showing cached','as of '))
    failure=re.search(r'failed to load usage|please run /login|not logged in|authentication failed|usage endpoint is rate limited',text,re.I)
    # Each reset stays with its own row. Unknown calendar dates are not invented.
    heading=re.compile(r'(?m)^\s*(Current session|Current week(?:\s*\([^\n)]+\))?|Spend limit)\s*:?')
    matches=list(heading.finditer(text));pools=[]
    for i,match in enumerate(matches):
        title=match[1];block=text[match.end():matches[i+1].start() if i+1<len(matches) else len(text)]
        percent=re.search(r'(\d+(?:\.\d+)?)%\s*(used|remaining)?',block)
        if not percent:continue
        value=float(percent[1]);remaining=percent[2]=='remaining';scope=re.search(r'\(([^)]+)\)',title)
        model=scope[1].removesuffix(' only').strip().lower() if scope else None
        identifier='five_hour' if title=='Current session' else 'spend_limit' if title=='Spend limit' else 'seven_day' if model in (None,'all models') else 'weekly/'+model
        models=['*'] if identifier in ('five_hour','seven_day','spend_limit') else [model] if model in ('opus','sonnet','haiku') else []
        reset=re.search(r'(?i)resets?\s+([^\n]+)',block);reset_note=reset[1].strip()[:256] if reset else None
        iso=re.search(r'\d{4}-\d\d-\d\dT\d\d:\d\d[^\s]*',reset_note or '')
        item=pool(identifier,used=None if remaining else value,remaining=value if remaining else None,
            percent_overage=identifier=='spend_limit',reset=timestamp(iso[0]) if iso else None,
            resolution=10**(-len(percent[1].split('.')[1])) if '.' in percent[1] else 1,
            duration=18000 if identifier=='five_hour' else 604800 if identifier.startswith(('weekly/','seven_day')) else None,
            models=models,membership='native-usage-row' if models else 'unknown',kind='provider-wide' if models==['*'] else 'model-specific',
            label=title,reset_note=reset_note)
        if cached or not fresh_transport:item['observed_at']=0
        pools.append(item)
    # A measured final pane can supersede an earlier startup banner. A failure
    # inside/after that pane remains a source error; it is never quota exhaustion.
    if not cached and failure and (not pools or failure.start()>=matches[0].start()):
        message=failure[0].lower()
        code='SOURCE_RATE_LIMITED' if 'rate limited' in message else 'SOURCE_ACCESS_DENIED' if any(s in message for s in ('/login','not logged in','authentication failed')) else 'SOURCE_UNAVAILABLE'
        raise PeerError(code,'Claude native usage reader could not obtain current quota')
    require(pools,'SOURCE_SCHEMA_MISMATCH','Claude returned no measured plan quota rows; session insights are not account quota')
    cache_age=re.search(r'(\d+)\s*(second|minute|hour)s?\s+ago',folded) if cached else None
    age=int(cache_age[1])*{'second':1,'minute':60,'hour':3600}[cache_age[2]] if cache_age else None
    metrics=re.search(r'Usage:\s*(\d+) input,\s*(\d+) output,\s*(\d+) cache read,\s*(\d+) cache write',text)
    zero_metrics=all(int(n)==0 for n in metrics.groups()) if metrics else None
    require(zero_metrics is not False,'SOURCE_EXECUTION_UNCONFIRMED','Collector panel reports nonzero invocation tokens')
    return dict(source='claude-native-usage',route='native-cli-auth',pools=pools,models=[],coverage='partial',
        collection=dict(outcome='CACHED' if cached else 'FRESH' if fresh_transport else 'UNVERIFIED',
            freshness_basis='native-last-known' if cached else 'native-panel-success' if fresh_transport else 'direct-transport-loses-native-cache-status',
            provider_observed_at=None,native_cache_age_seconds=age,native_zero_invocation_tokens=zero_metrics),
        limitations=['Each visible row retains its own reset; omitted rows/credits remain unknown.',
            'Reset text without a full provider date is retained without inventing an epoch.'])
