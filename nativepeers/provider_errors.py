"""Provider diagnostics become precise failures, never account/model substitutions."""
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo


EXACT={
    'AUTH_REQUIRED':('auth_required','authentication_error','authentication_failed','invalid_api_key','unauthorized','not_authenticated'),
    'QUOTA_EXHAUSTED':('quota_exhausted','insufficient_quota','usage_limit_reached','enforced_spend_limit_reached','billing_hard_limit_reached'),
    'RATE_LIMITED':('rate_limit_error','rate_limited','too_many_requests'),
    'MODEL_UNAVAILABLE':('model_not_found','model_unavailable'),
    'EFFORT_UNSUPPORTED':('effort_unsupported',),
    'BUDGET_EXHAUSTED':('error_max_turns','max_turns','max_budget'),
}


def classify(error):
    """Classify the terminal error, not logging/context serialized beside it."""
    if isinstance(error,dict):
        terminal=error.get('terminal')
        if isinstance(terminal,dict):
            terminal_code=classify(terminal)
            if terminal_code!='PROVIDER_TASK_FAILED':return terminal_code
        codes=[error.get(k) for k in ('error_code','code','type','subtype','error')]
        exact=next((code for item in codes if isinstance(item,str)
            for code,values in EXACT.items() if item.lower() in values),None)
        nested_rate=False
        if exact and exact!='RATE_LIMITED':return exact
        for key in ('error','details','cause'):
            child=error.get(key)
            if isinstance(child,dict):
                code=classify(child)
                # Structured spend limits refine generic throttling, but an
                # incidental historical auth diagnostic cannot replace it.
                if exact=='RATE_LIMITED':
                    if code=='QUOTA_EXHAUSTED':return code
                elif code!='PROVIDER_TASK_FAILED':return code
                nested_rate=nested_rate or code=='RATE_LIMITED'
        # Only diagnostic fields are text. Account IDs, stack traces, context
        # and successful-auth banners cannot become an error diagnosis.
        messages=[error[k] for k in ('message','result','description','error') if isinstance(error.get(k),str)]
        message=error.get('message')
        if isinstance(message,dict):
            messages.extend(c['text'] for c in message.get('content',[]) if isinstance(c,dict) and c.get('type')=='text' and isinstance(c.get('text'),str))
        text=' '.join(messages).lower()
        status=next((error[k] for k in ('status','status_code','http_status','code') if error.get(k) in (401,429)),None)
    else:
        text=error.lower() if isinstance(error,str) else '';exact=None;status=None;nested_rate=False
    patterns=[
        ('AUTH_REQUIRED',r'\b(?:unauthorized|invalid_api_key|not logged in|authentication (?:failed|required|error)|please (?:sign in|log in|run /login))\b'),
        ('MODEL_UNAVAILABLE',r'model_not_found|model unavailable|model is not available|unknown model'),
        ('EFFORT_UNSUPPORTED',r'unsupported reasoning|unsupported effort'),
        ('QUOTA_EXHAUSTED',r'usage limit(?: reached| exceeded)?|insufficient_quota|quota (?:exhausted|exceeded|limit exceeded)|credit balance (?:is )?(?:too low|exhausted)|you.ve hit your limit|usage exhausted|limit reached · resets'),
        ('RATE_LIMITED',r'rate_limit|rate limit'),
        ('APPROVAL_BLOCKED',r'permission denied|approval required|tool denied'),
        ('BUDGET_EXHAUSTED',r'max_turns|max turns|max_budget'),
    ]
    matched={code for code,pattern in patterns if re.search(pattern,text)}
    if exact=='RATE_LIMITED':return 'QUOTA_EXHAUSTED' if 'QUOTA_EXHAUSTED' in matched and not matched-{'QUOTA_EXHAUSTED','RATE_LIMITED'} else exact
    if len(matched-{'RATE_LIMITED'})>1:return 'PROVIDER_TASK_FAILED'
    if matched:return next(code for code,_ in patterns if code in matched)
    if exact:return exact
    if nested_rate:return 'RATE_LIMITED'
    if status:return {401:'AUTH_REQUIRED',429:'RATE_LIMITED'}[status]
    http=re.search(r'\bhttp(?:\s+(?:status|error))?\s+(401|429)(?!\d)',text)
    return {'401':'AUTH_REQUIRED','429':'RATE_LIMITED'}[http[1]] if http else 'PROVIDER_TASK_FAILED'


def reset_time(error):
    """Format only a provider-supplied reset; never invent an account deadline."""
    text=json.dumps(error,ensure_ascii=False)
    pending=[error];values=[]
    for _ in range(128):
        if not pending:break
        item=pending.pop()
        if isinstance(item,dict):
            values.extend(item.get(key) for key in ('reset_at','resets_at'))
            pending.extend(value for value in item.values() if isinstance(value,(dict,list)))
        elif isinstance(item,list):pending.extend(item[:128])
    for value in values:
        if not isinstance(value,(str,int,float)) or isinstance(value,bool):continue
        try:
            date=datetime.fromtimestamp(value,ZoneInfo('America/New_York')) if isinstance(value,(int,float)) else datetime.fromisoformat(value.replace('Z','+00:00'))
            if date.tzinfo is not None:return date.astimezone(ZoneInfo('America/New_York')).strftime('%Y-%m-%d %-I:%M %p %Z')
        except (ValueError,TypeError,OverflowError,OSError):pass
    match=re.search(r'resets?\s+(?:at\s+)?(\d{1,2})(?::(\d\d))?\s*(am|pm)\s*\((America/New_York|US/Eastern)\)',text,re.I)
    if match:
        hour,minute,period=int(match[1]),int(match[2] or 0),match[3].upper()
        if 1<=hour<=12 and 0<=minute<60:
            # Time and zone are supplied, but no date/season is inferred.
            return f'{hour}:{minute:02d} {period} Eastern (provider did not supply a date)'
    return None


def diagnostic(error,*,submitted=False,code=None,provider='claude'):
    """Bounded structured provider error, distinct from model output."""
    code=code or classify(error)
    name={'claude':'Claude','antigravity':'Antigravity','codex':'Codex'}.get(provider,'The provider')
    messages={'AUTH_REQUIRED':name+' native login is unavailable for the selected home.',
              'QUOTA_EXHAUSTED':name+' usage limit reached.', 'RATE_LIMITED':'The provider rate-limited this task.'}
    raw=json.dumps(error,ensure_ascii=False)
    value={'code':code,'message':messages.get(code,'The native provider could not complete this task.'),
           'may_have_received':submitted,'native_diagnostic':json.loads(raw) if len(raw)<=16000 else {'text':raw[:8000],'truncated':True}}
    reset=reset_time(error)
    if reset:value.update(reset_time=reset,message=value['message']+' Provider reset: '+reset+'.')
    return value
