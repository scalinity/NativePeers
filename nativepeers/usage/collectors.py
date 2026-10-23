"""Supported metadata only. No thread, turn, login, credit redemption or API key reads."""
import asyncio
from pathlib import Path

from ..adapters.codex_app_server import AppServer,ProviderError
from ..compatibility import verify,fingerprint
from ..errors import PeerError,require
import json
import time
from ..launch import home_environment
from .normalize import codex


def executable(provider):
    return Path.home()/{'codex':'.local/share/codex-mcp-manager/current/bin/codex',
        'claude':'.local/bin/claude','antigravity':'.local/bin/agy'}[provider]


def resolved_executable(provider):
    try:return executable(provider).resolve(strict=True)
    except FileNotFoundError as error:
        raise PeerError('EXECUTABLE_NOT_FOUND','The configured native executable was not found') from error


def verify_usage(journal,provider,path):
    """A reverified status-line contract does not approve worker/inbox execution."""
    try:return verify(journal,provider,path)
    except PeerError as error:
        if error.code!='CAPABILITY_MISMATCH' or provider=='codex':raise
        receipt=journal.root/'compat/usage-runtime.json'
        scoped=json.loads(receipt.read_text()).get(provider) if receipt.exists() else None
        actual=fingerprint(path)
        require(scoped and scoped.get('contract')=='native-statusline-v1' and scoped.get('proof')=='verified-zero-input-native-statusline'
            and all(scoped.get('runtime',{}).get(k)==actual[k] for k in ('path','sha256','size')),
            'CAPABILITY_MISMATCH','Usage status-line contract has not been verified for this executable')
        return actual


def model_metadata(rows):
    result=[]
    if not isinstance(rows,list):return result
    for row in rows[:64]:
        if not isinstance(row,dict) or not isinstance(row.get('model'),str):continue
        efforts=row.get('supportedReasoningEfforts');modalities=row.get('inputModalities')
        result.append(dict(model=row['model'][:512],display_name=str(row.get('displayName',row['model']))[:512],
            efforts=[x['reasoningEffort'][:64] for x in (efforts[:16] if isinstance(efforts,list) else [])
                     if isinstance(x,dict) and isinstance(x.get('reasoningEffort'),str)],
            modalities=[x[:64] for x in (modalities[:8] if isinstance(modalities,list) else []) if isinstance(x,str)],
            capability_source='native-model-catalog',support='NATIVE_CATALOG',entitlement='UNVERIFIED'))
    return result


class CodexCollector:
    source='codex-rpc'

    async def collect(self,journal,home):
        path=resolved_executable('codex');runtime=verify(journal,'codex',path)
        server=AppServer(path,environment=home_environment('codex',home))
        try:
            async with asyncio.timeout(6):
                await server.start()
                account=(await server.call('account/read',{'refreshToken':False},timeout=2)).get('account') or {}
                api_key=account.get('type')=='apiKey'
                value={} if api_key else await server.call('account/rateLimits/read',{'excludeResetCreditDetails':True},timeout=5)
                require(api_key or isinstance(value,dict) and (isinstance(value.get('rateLimitsByLimitId'),dict) or
                    isinstance(value.get('rateLimits'),dict) or type(value.get('ordinaryUsageAllowed')) is bool),
                    'SOURCE_SCHEMA_MISMATCH','Native quota RPC returned no recognized account quota fields')
            # Optional enrichment has its own small budget and cannot discard quota.
            try:
                async with asyncio.timeout(1):models=(await server.call('model/list',{'includeHidden':True,'limit':100},timeout=1))['data']
            except (ProviderError,ConnectionError,TimeoutError,KeyError,TypeError,AttributeError):models=[]
            return dict(source=self.source,source_version=runtime,route='api-key' if api_key else 'subscription' if account.get('type')=='chatgpt' else 'native-auth-unknown',
                pools=codex(value),models=model_metadata(models),coverage='unknown' if api_key else 'partial',
                collection=dict(outcome='UNAVAILABLE' if api_key else 'FRESH',freshness_basis='native-quota-rpc',provider_observed_at=None),
                limitations=['API-key billing has no subscription allowance.' if api_key else 'The native model catalog does not establish entitlement or complete bucket membership.',
                    'Reset credits are reported only; no credit is consumed or billing setting changed.']+
                    ([] if models else ['Optional model catalog unavailable; quota result retained.']))
        finally:
            await server.close(exit_timeout=1,kill_timeout=1,reader_timeout=.5)



class NativeCollector:
    def __init__(self,provider):self.provider=provider

    async def collect(self,journal,home,*,capture_directory=None):
        from .contracts import verified
        from .native_command import run_native
        binary=resolved_executable(self.provider);receipt=verified(journal,self.provider,binary)
        if self.provider=='antigravity':
            from ..launch import provider_home
            from .antigravity_native import parse
            require(Path(home).resolve()==provider_home('antigravity'),'SOURCE_HOME_MISMATCH','Antigravity quota reader has no alternate-home launch contract')
            started=time.monotonic()
            result=await run_native(self.provider,binary,['-p','/usage','--output-format','json','--print-timeout','15s'],home,timeout=20)
            payload=parse(result['stdout'],fresh_verified=receipt.get('fresh_fetch_contract')=='native-print-usage-force-refresh-v1')
            require(result['exit_code']==0,'SOURCE_UNAVAILABLE','Native usage command exited unsuccessfully')
            measured=time.time();payload['collection']['quota_completed_at']=measured
            for item in payload['pools']:item.setdefault('observed_at',measured)
            if receipt.get('credits_contract')=='remaining_credits-v1' and 20-(time.monotonic()-started)>3:
                from .antigravity_native import credits
                try:
                    remaining=20-(time.monotonic()-started)
                    credit_result=await run_native(self.provider,binary,['-p','/credits','--output-format','json',
                        '--print-timeout',str(max(1,int(min(15,remaining-2))))+'s'],home,timeout=remaining)
                    require(credit_result['exit_code']==0,'SOURCE_UNAVAILABLE','Native credits command exited unsuccessfully')
                    payload['pools'].append(dict(credits(credit_result['stdout']),observed_at=time.time()))
                    payload['collection'].update(credits_coverage='REPORTED',credits_cleanup=credit_result['cleanup'])
                    payload['limitations'][-1]='Additional credits are separate; they do not override exhausted included-usage pools.'
                except PeerError as error:
                    payload['collection']['credits_error']=error.code
                    payload['limitations'].append('Optional credits read failed; measured quota was retained.')
        else:
            from .claude_native import parse
            import uuid
            from ..profiles import ROOT
            from ..files import private_directory
            collector_id=str(uuid.uuid4())
            args=['--safe-mode','--tools','','--strict-mcp-config','--settings','{"remoteControlAtStartup":false,"disableAllHooks":true}',
                '--session-id',collector_id]
            # Use a private child directory beneath the existing project. This
            # inherits only existing native trust; no trust entry is created.
            cwd_parent=private_directory(ROOT/'evidence/private/usage-collector-cwds')
            try:result=await run_native(self.provider,binary,args,home,timeout=15,terminal=True,capture_directory=capture_directory,cwd_parent=cwd_parent)
            except PeerError as error:
                error.details=dict(error.details or {},collector_uuid=collector_id,
                    conversation_created=any((Path(home)/'projects').glob('*/'+collector_id+'.jsonl')))
                raise
            require(result['submitted_commands']==1,'SOURCE_NOT_QUALIFIED','Collector did not invoke exactly one native usage command')
            payload=parse(result['stdout'].decode(errors='replace'),fresh_transport=True)
            # Read only exact collector history names, never another conversation.
            retained=any((Path(home)/'projects').glob('*/'+collector_id+'.jsonl'))
            require(not retained,'SOURCE_PERSISTENCE_DETECTED','Native usage collector retained a conversation; it was preserved',
                details={'collector_uuid':collector_id})
            payload['collection'].update(collector_uuid=collector_id,conversation_created=False,num_turns=None,
                no_inference_basis='inspected-local-command-dispatch; one usage command; no task/prompt',
                history_check='exact-collector-uuid-in-selected-home')
        payload['source_version']=receipt['runtime'];payload['collection']['cleanup']=result['cleanup']
        return payload


COLLECTORS={'codex':CodexCollector(),'claude':NativeCollector('claude'),
            'antigravity':NativeCollector('antigravity')}
