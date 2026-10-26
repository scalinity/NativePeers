"""Explicit, zero-input renewal inside a bounded native CLI support envelope."""
import json
import mmap
import re
import subprocess
import sys
from pathlib import Path

from .compatibility import fingerprint, update_verified, verify
from .errors import PeerError, require
from .files import advisory_lock, atomic_json

# These are adapter support policies, not vendor promises of binary equivalence.
# Managed Codex has local queue/history patches and needs its manager qualification.
CONTRACTS={
    'claude':dict(series=(2,1),minimum=(2,1,310),team='Q6L2SF6YDW',identifier='com.anthropic.claude-code',
        flags=['--print','--output-format','--model','--permission-mode','--tools','--allowedTools',
               '--strict-mcp-config','--mcp-config','--settings','--session-id','--resume','--name','--restricted'],
        markers=['stream-json','dontAsk','tool_use','PreToolUse','SessionStart','permission_mode',
                 'peer_message_status','orig_msg_id','msgV']),
    'antigravity':dict(series=(1,4),minimum=(1,4,5),team='EQHXZ8M8AV',identifier='cli',
        flags=['--input-format','--output-format','--model','--agent','--sandbox','--print-timeout','--conversation'],
        markers=['stream-json','PreToolUse','PreInvocation','SessionStart','toolCall','transcriptPath','conversationId','decision']),
    'grok':dict(series=(1,0),minimum=(1,0,70),team='5Y6N3AJ54S',identifier='xai-grok-pager',
        flags=['--output-format','--model','--sandbox','--permission-mode','--tools','--no-subagents',
               '--disable-web-search','--disallowed-tools','--deny','--max-turns','--no-auto-update'],
        markers=['streaming-json','PreToolUse','SessionStart','additionalContext','dontAsk']),
}
READ_ONLY_PROFILE='(version 1)(allow default)(deny network*)(deny file-write*)'


def version(value):
    match=re.search(r'\b(\d+)\.(\d+)\.(\d+)(?=[\s(]|$)',value)
    return tuple(map(int,match.groups())) if match else None


def check_runtime(journal,provider,*,apply=False):
    """Inspect by default. --apply renews only worker/inbox approval, never quota."""
    from .usage.collectors import executable
    selected=executable(provider)
    try:runtime=fingerprint(selected,fresh=True)
    except OSError:
        raise PeerError('EXECUTABLE_NOT_FOUND','The selected native executable is unavailable') from None
    path=journal.root/'compat/runtime.json'
    document=json.loads(path.read_text()) if path.exists() else {}
    prior=document.get('providers',{}).get(provider)
    result=dict(provider=provider,runtime=runtime,operations=['worker_turns','inbox'],model_inputs=0,
                native_acceptance='NOT_RUN',approved=False,approval_written=False,checks=[])

    def refuse(state,reason):
        if apply and state in ('INCOMPATIBLE','PROVENANCE_UNVERIFIED'):
            # Remember an observed failed check, never an approval. Read doctor
            # remains passive and a later executable does not inherit this result.
            with advisory_lock(journal.root/'locks/runtime.lock'):
                current=json.loads(path.read_text())
                require(current.get('providers',{}).get(provider)==prior and fingerprint(selected,fresh=True)==runtime,
                        'CAPABILITY_MISMATCH','Runtime or approval changed during the failed check',
                        details={'compatibility_state':'UPDATE_RACED'})
                current.setdefault('update_checks',{})[provider]=dict(runtime=runtime,compatibility_state=state,reason=reason)
                atomic_json(path,current)
        raise PeerError('CAPABILITY_MISMATCH',reason,details=dict(result,compatibility_state=state,reason=reason))

    retained=document.get('retained',{}).get(provider,{}).get(runtime['sha256'],{})
    if retained.get('revoked'):refuse('REVOKED','The selected runtime was explicitly revoked; renewal cannot override it')
    if prior and prior.get('sha256')==runtime['sha256']:
        verify(journal,provider,selected)
        return dict(result,approved=True,compatibility_state='APPROVED',reason='The selected runtime is already approved')
    if not prior:refuse('QUALIFICATION_REQUIRED','Initial worker/inbox approval requires native integration qualification')
    if retained:refuse('QUALIFICATION_REQUIRED','A retired runtime cannot be adopted as a routine update')
    if provider not in CONTRACTS:
        refuse('QUALIFICATION_REQUIRED','Managed Codex updates require the manager\'s affected queue, hook and worker-history qualification')
    contract=CONTRACTS[provider]
    previous=version(prior.get('version',''))
    if not previous or previous[:2]!=contract['series'] or previous<contract['minimum']:
        refuse('QUALIFICATION_REQUIRED','The previous approval is outside the supported adapter release series')
    if sys.platform!='darwin':refuse('CHECK_UNAVAILABLE','Routine renewal requires the macOS vendor signature and read-only execution guard')

    def run(argv):
        try:
            # Fixed metadata commands only. No native prompt, stdin or home override.
            value=subprocess.run(argv,stdin=subprocess.DEVNULL,capture_output=True,timeout=5,
                                 cwd=Path(__file__).resolve().parent)
        except (OSError,subprocess.TimeoutExpired):
            refuse('CHECK_UNAVAILABLE','A bounded signature or CLI metadata check could not complete')
        if len(value.stdout)+len(value.stderr)>262144:refuse('CHECK_UNAVAILABLE','CLI metadata exceeded the 256 KiB bound')
        return value

    requirement=f'anchor apple generic and certificate leaf[subject.OU] = "{contract["team"]}" and identifier "{contract["identifier"]}"'
    signed=run(['/usr/bin/codesign','--verify','--strict','-R='+requirement,runtime['path']])
    if signed.returncode:refuse('PROVENANCE_UNVERIFIED','The selected executable does not satisfy the vendor code-signature requirement')
    result['checks'].append('vendor-signature')
    def metadata(flag):
        value=run(['/usr/bin/sandbox-exec','-p',READ_ONLY_PROFILE,runtime['path'],flag])
        if value.returncode:refuse('CHECK_UNAVAILABLE','Native '+flag+' failed under the no-network, no-write guard')
        return (value.stdout+value.stderr).decode('utf-8',errors='replace')
    observed=metadata('--version').strip();numbers=version(observed)
    if not numbers or numbers[:2]!=contract['series'] or numbers<max(previous,contract['minimum']):
        refuse('QUALIFICATION_REQUIRED','This release is outside the supported forward patch-update envelope')
    result.update(version=observed,checks=result['checks']+['supported-forward-patch'])
    help_text=metadata('--help')
    missing=[flag for flag in contract['flags'] if not re.search(re.escape(flag)+r'(?=[\s,=<]|$)',help_text)]
    if missing:refuse('INCOMPATIBLE','Required native CLI options are absent: '+', '.join(missing))
    with Path(runtime['path']).open('rb') as stream, mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_READ) as source:
        missing=[marker for marker in contract['markers'] if source.find(marker.encode())<0]
    if missing:refuse('INCOMPATIBLE','Required adapter protocol/permission markers are absent: '+', '.join(missing))
    result['checks']+=['required-cli-options','adapter-contract-markers']
    require(fingerprint(selected,fresh=True)==runtime,'CAPABILITY_MISMATCH','Selected runtime changed during validation',
            details=dict(compatibility_state='UPDATE_RACED',operations=result['operations']))
    result.update(compatibility_state='SUPPORTED_UPDATE',reason='Vendor-signed forward patch retains the bounded adapter contract')
    if apply:
        approval=dict(runtime,version=observed,operations=result['operations'],scope='Bounded signed patch-update checks; native acceptance NOT_RUN',
                      update_basis=dict(previous=prior['sha256'],checks=result['checks']))
        update_verified(journal,{provider:approval},expected={provider:prior},selected={provider:selected})
        result['approval_written']=True
        require(fingerprint(selected,fresh=True)==runtime,'CAPABILITY_MISMATCH','Updater changed the selected runtime after approval; run runtime-check again',
                details=dict(result,compatibility_state='UPDATE_RACED'))
        verify(journal,provider,selected)
        result.update(approved=True,compatibility_state='APPROVED')
    return result
