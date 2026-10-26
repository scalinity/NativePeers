"""Owned Grok conversation telemetry; no account-quota inference."""
import json

from ..compatibility import verify
from ..errors import PeerError, require


def parse(value, native_id, *, turn=None):
    try:
        data=json.loads(value) if isinstance(value,(str,bytes)) else value
    except (ValueError,TypeError):
        raise PeerError('SOURCE_SCHEMA_MISMATCH','Grok returned invalid persisted usage') from None
    require(isinstance(data,dict) and data.get('sessionId')==native_id,
        'SOURCE_SCHEMA_MISMATCH','Grok usage does not match the owned native UUID')
    turns=data.get('turns')
    require(isinstance(turns,list) and 0<len(turns)<=10000,
        'SOURCE_SCHEMA_MISMATCH','Grok usage has no bounded turn records')
    require(all(isinstance(row,dict) and type(row.get('turnNumber')) is int and row['turnNumber']>0 for row in turns),
        'SOURCE_SCHEMA_MISMATCH','Grok usage has invalid turn numbers')
    numbers=[row['turnNumber'] for row in turns]
    require(len(numbers)==len(set(numbers)),'SOURCE_SCHEMA_MISMATCH','Grok usage repeats a turn number')
    if turn is None:
        selected=max(turns,key=lambda row:row['turnNumber'])
    else:
        require(type(turn) is int and turn>0,'PROTOCOL_ERROR','Select a positive native Grok turn number')
        selected=next((row for row in turns if row['turnNumber']==turn),None)
        require(selected is not None,'SOURCE_SCHEMA_MISMATCH','The requested Grok usage turn is absent')
    result={}
    for source,target in [('inputTokens','input_tokens'),('outputTokens','output_tokens'),
        ('cachedReadTokens','cache_read_input_tokens'),('cacheCreationTokens','cache_creation_input_tokens'),
        ('reasoningTokens','reasoning_tokens'),('totalTokens','total_tokens')]:
        value=selected.get(source)
        require(type(value) is int and value>=0,'SOURCE_SCHEMA_MISMATCH','Invalid Grok usage count: '+source)
        result[target]=value
    # Native inputTokens includes cached reads. Keep both reported values; do
    # not add cache counts to the provider's total a second time.
    result.update(source='grok-usage-session',native_session_id=native_id,
        native_turn_number=selected['turnNumber'],counter_scope='native-turn',
        engine_model=selected.get('primaryModelId'))
    return result


async def session_usage(journal, home, native_id, executable, cwd, *, turn=None):
    require(journal.one("SELECT worker_id FROM workers WHERE provider='grok' AND native_id=?",(native_id,)),
        'OWNERSHIP_UNKNOWN','Grok session usage requires an owned worker UUID')
    verify(journal,'grok',executable)
    from .native_command import run_native
    arguments=['--no-auto-update','usage',native_id]
    if turn is not None:arguments.append(str(turn))
    response=await run_native('grok',executable,arguments,home,timeout=10)
    require(response['exit_code']==0,'SOURCE_UNAVAILABLE','Grok persisted usage could not be read')
    result=parse(response['stdout'],native_id,turn=turn)
    verify(journal,'grok',executable)
    return result
