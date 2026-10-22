"""Provider diagnostics become precise failures, never account/model substitutions."""
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo


def classify(error):
    text=json.dumps(error,ensure_ascii=False).lower()
    for code,terms in [
        ('AUTH_REQUIRED',('unauthorized','authentication','invalid_api_key','not logged in')),
        ('MODEL_UNAVAILABLE',('model_not_found','model unavailable','model is not available','unknown model')),
        ('EFFORT_UNSUPPORTED',('unsupported reasoning','unsupported effort')),
        ('QUOTA_EXHAUSTED',('usage limit','quota','insufficient_quota','credit balance',"you've hit your limit",'usage exhausted','limit reached · resets')),
        ('RATE_LIMITED',('rate_limit','rate limit')),
        ('APPROVAL_BLOCKED',('permission denied','approval required','tool denied')),
        ('BUDGET_EXHAUSTED',('max_turns','max turns','max_budget')),
    ]:
        if any(term in text for term in terms):return code
    # Bare digits also occur in native UUIDs/timestamps. Only a stated HTTP
    # status can supply a numeric diagnosis.
    status=re.search(r'(?:\bhttp(?:\s+(?:status|error))?\s+|"(?:status|status_code|http_status|code)"\s*:\s*"?)(401|429)(?!\d)',text)
    if status:return {'401':'AUTH_REQUIRED','429':'RATE_LIMITED'}[status[1]]
    return 'PROVIDER_TASK_FAILED'


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


def diagnostic(error,*,submitted=False):
    """Bounded structured provider error, distinct from model output."""
    code=classify(error)
    messages={'AUTH_REQUIRED':'Claude authentication is required; use the native login flow.',
              'QUOTA_EXHAUSTED':'Claude usage limit reached.', 'RATE_LIMITED':'The provider rate-limited this task.'}
    raw=json.dumps(error,ensure_ascii=False)
    value={'code':code,'message':messages.get(code,'The native provider could not complete this task.'),
           'may_have_received':submitted,'native_diagnostic':json.loads(raw) if len(raw)<=16000 else {'text':raw[:8000],'truncated':True}}
    reset=reset_time(error)
    if reset:value.update(reset_time=reset,message=value['message']+' Provider reset: '+reset+'.')
    return value
