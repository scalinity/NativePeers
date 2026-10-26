"""Grok-native metadata and queued hook delivery; imported histories are excluded."""
import os
from pathlib import Path
import json
import uuid

from .errors import require

HOOK_CONTEXT_LIMIT=10000
HOOK_OUTPUT_LIMIT=65536


def context_characters(text):
    """Use the conservative UTF-16 bound for native character clipping."""
    return len(text.encode('utf-16-le'))//2


def hook_frame(message):
    """Keep a complete frame within Grok's native character and output limits."""
    from .files import canonical
    from .presentation import render
    text=render(message)
    characters=context_characters(text)
    require(characters<=HOOK_CONTEXT_LIMIT and len(canonical({'hookSpecificOutput':{'hookEventName':'PreToolUse','additionalContext':text}}))<=HOOK_OUTPUT_LIMIT,
            'PAYLOAD_TOO_LARGE','Rendered Grok message exceeds the native hook context limit',
            details=dict(rendered_characters=characters,character_unit='UTF-16 code units',context_character_limit=HOOK_CONTEXT_LIMIT,output_byte_limit=HOOK_OUTPUT_LIMIT))
    return text


def native_home():
    return Path(os.getenv('GROK_HOME',Path.home()/'.grok')).expanduser().resolve()


def executable():
    primary=Path.home()/'.grok/bin/grok'
    return (primary if primary.exists() else Path.home()/'.local/bin/grok').resolve()


def summaries(*,native_id=None,title=None,home=None):
    require(native_id is not None or title is not None,'PROTOCOL_ERROR','An exact native ID or title is required')
    root=(Path(home) if home is not None else native_home()).resolve()/'sessions'
    if not root.is_dir():return []
    if native_id is not None:
        try:require(str(uuid.UUID(native_id))==native_id,'PROTOCOL_ERROR','Invalid Grok native UUID')
        except (ValueError,TypeError,AttributeError):require(False,'PROTOCOL_ERROR','Invalid Grok native UUID')
    rows=[]
    for path in root.glob('*/'+(native_id or '*')+'/summary.json'):
        if path.is_symlink() or path.parent.is_symlink() or path.parent.parent.is_symlink() or not path.resolve().is_relative_to(root):continue
        try:
            # Summary metadata only. Native chat and imported Claude/Codex stores
            # are never searched to find a title or infer an owner.
            if path.stat().st_size>262144:continue
            value=json.loads(path.read_text());info=value['info'];native=info['id']
            if str(uuid.UUID(native))!=native or path.parent.name!=native:continue
            cwd=info['cwd']
            if not isinstance(cwd,str) or not Path(cwd).is_absolute():continue
            name=info.get('title') or value.get('title') or value.get('session_summary') or None
            if name is not None and not isinstance(name,str):continue
            if title is not None and name!=title:continue
            rows.append(dict(conversation_id=native,title=name,model=value.get('current_model_id'),
                             cwd=cwd,path=str(path.resolve()),sandbox_profile=value.get('sandbox_profile')))
        except (OSError,ValueError,KeyError,TypeError,AttributeError):continue
    return rows


def resolve_name(name):
    rows=summaries(title=name)
    require(rows,'TARGET_OFFLINE','No Grok-native session has that exact title')
    require(len(rows)==1,'AMBIGUOUS_TARGET','Several Grok-native histories have that title; use a native UUID',details=rows)
    return rows[0]['conversation_id']


async def queue_native_hook(peer,message):
    hook_frame(message)
    return dict(stage='SUBMITTED_UNCONFIRMED',evidence_kind='local-journal-pending-grok-hook',
                native_wakeup=False,message_id=message['message_id'],
                limitation='Pending the exact owner next native tool invocation; no idle wakeup or ingestion is claimed.')
