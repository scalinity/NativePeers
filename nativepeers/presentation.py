"""Readable native chat input; the immutable envelope stays in the local journal."""
from datetime import datetime
from zoneinfo import ZoneInfo
from .files import sha256


def render(message):
    actor=message['from']
    source=('delegation '+actor['group_id'] if actor.get('kind')=='controller' else actor.get('peer_key','local origin'))
    heading='NativePeers · '+('Worker results' if message.get('kind')=='completion' else 'Reply' if message.get('in_reply_to') else 'Message')
    lines=[heading,'',message['body']['text'],'','— NativePeers references —',
           'From: '+source,'Message: '+message['message_id'],'Exchange: '+message['exchange_id']]
    if message.get('in_reply_to'):lines.append('In reply to: '+message['in_reply_to'])
    if message.get('artifact_refs'):lines.append('Artifacts: '+', '.join(message['artifact_refs']))
    if message.get('task_id'):lines.append('Task: '+message['task_id'])
    if message.get('expires_at') is not None:
        expires=datetime.fromtimestamp(message['expires_at'],ZoneInfo('America/New_York')).strftime('%Y-%m-%d %-I:%M:%S %p %Z')
        lines.append('Admission expiry: '+expires+' (native ingestion can occur later)')
    if message.get('expect_reply'):lines.append('Reply: npeer reply '+message['message_id']+' --message "YOUR REPLY" --client-request-id NEW_ID --json')
    lines.extend(['Full routing record: npeer status '+message['message_id']+' --json',
                  'Envelope: v1 / '+sha256(message)])
    return '\n'.join(lines)


def matches(text,message):
    """Require the complete deterministic frame, not a matching ID or body alone."""
    expected=render(message)
    return ('\n'+expected+'\n') in ('\n'+text+'\n')


def completion(group_id,generation,results,summary_limit=2000,title_limit=512,*,context=None):
    lines=['Delegation '+group_id+' · completion '+str(generation),'Independently produced worker results.']
    if context:
        lines.append('Group context at notice preparation: initial '+str(context['initial'])+'; latest phase '+str(context['latest_phase'])+' '+str(context['latest'])+'; active '+str(context['active'])+'; queued '+str(context['queued'])+'. Historical outcomes remain unchanged.')
    for result in results:
        worker=result['worker'];model=result.get('model_observed') or 'not observed'
        requested=result.get('model_requested') or 'not recorded'
        model=model[:128]+('…' if len(model)>128 else '')
        requested=requested[:128]+('…' if len(requested)>128 else '')
        summary=result.get('summary') or (result.get('error') or {}).get('message') or (result.get('error') or {}).get('code') or 'No result text was produced; inspect the retained result.'
        clipped=summary[:summary_limit]
        if len(summary)>summary_limit:clipped+='\n[Summary shortened; full text is retained.]'
        name=worker['title'][:title_limit]+('…' if len(worker['title'])>title_limit else '')
        lines+=['',name+' · '+result['status'].upper(),
                worker['provider']+' / requested '+requested+' / observed '+model,clipped]
        lines.append('Task: '+str(result.get('task_id','not recorded'))+' · phase '+str(result.get('completion_phase',generation)))
        error=result.get('error') or {}
        if error:
            lines.append('Reason: '+str(error.get('code','unknown'))+' · '+str(error.get('message','Inspect full diagnostics'))[:400])
            if error.get('limit'):
                limit=error['limit'];observed=limit.get('observed')
                lines.append('Budget: '+str(limit.get('field'))+' configured '+str(limit.get('value'))+'; observed '+(str(observed) if observed is not None else 'not available')+'. Logical inputs and wall deadlines are separate.')
            lines.append('Input may have executed: '+str(error.get('may_have_received','unknown'))+'. Inspect retained evidence before deciding recovery; do not replay accepted or uncertain input.')
        if result.get('task_id'):lines.append('Inspect: npeer result '+result['task_id']+' --compact --json')
        fallback=result.get('fallback')
        if fallback:lines.append('Fallback from '+fallback['primary']['provider']+'/'+fallback['primary']['model']+': '+fallback['reason'].rstrip('.')+' ('+str(len(result.get('attempts',[])))+' retained attempts).')
        lines.append('Native session: '+str(worker.get('native_session_id') or 'not created'))
        lines.append('Result: '+str(result['result_artifact_id'] or 'retained task disposition; no model result artifact'))
    lines+=['','Read full results: npeer result '+group_id+' --json']
    return '\n'.join(lines)
