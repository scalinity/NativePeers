"""Supported metadata only. No thread, turn, login, credit redemption or API key reads."""
import asyncio
from pathlib import Path

from ..adapters.codex_app_server import AppServer
from ..compatibility import verify,fingerprint
from ..errors import PeerError,require
import json
from ..launch import home_environment
from .normalize import codex


def executable(provider):
    return Path.home()/{'codex':'.local/share/codex-mcp-manager/current/bin/codex',
        'claude':'.local/bin/claude','antigravity':'.local/bin/agy'}[provider]


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
    for row in rows[:64]:
        if not isinstance(row,dict) or not isinstance(row.get('model'),str):continue
        result.append(dict(model=row['model'][:512],display_name=str(row.get('displayName',row['model']))[:512],
            efforts=[x['reasoningEffort'] for x in row.get('supportedReasoningEfforts',[])[:16]
                     if isinstance(x,dict) and isinstance(x.get('reasoningEffort'),str)],
            modalities=[x for x in row.get('inputModalities',[])[:8] if isinstance(x,str)],
            capability_source='native-model-catalog',support='NATIVE_CATALOG',entitlement='UNVERIFIED'))
    return result


class CodexCollector:
    source='codex-rpc'

    async def collect(self,journal,home):
        path=executable('codex').resolve(strict=True);runtime=verify(journal,'codex',path)
        server=AppServer(path,environment=home_environment('codex',home))
        try:
            async with asyncio.timeout(8):
                await server.start()
                account=(await server.call('account/read',{'refreshToken':False},timeout=3)).get('account') or {}
                if account.get('type')=='apiKey':
                    models=(await server.call('model/list',{'includeHidden':True,'limit':100},timeout=3))['data']
                    return dict(source=self.source,source_version=runtime,route='api-key',pools=[],models=model_metadata(models),
                        coverage='unknown',limitations=['Subscription allowance is unavailable on this native API-key billing route; local token metrics are separate.'])
                value=await server.call('account/rateLimits/read',{'excludeResetCreditDetails':True},timeout=5)
                models=(await server.call('model/list',{'includeHidden':True,'limit':100},timeout=3))['data']
            return dict(source=self.source,source_version=runtime,route='subscription' if account.get('type')=='chatgpt' else
                'api-key' if account.get('type')=='apiKey' else 'native-auth-unknown',
                pools=codex(value),models=model_metadata(models),coverage='partial',
                limitations=['The native model catalog does not establish entitlement or complete bucket membership.',
                    'Reset credits are reported only; no credit is consumed or billing setting changed.'])
        finally:
            await server.close(exit_timeout=1,kill_timeout=1,reader_timeout=.5)


class TelemetryCollector:
    def __init__(self,provider):self.provider=provider;self.source=provider+'-statusline'

    async def collect(self,journal,home):
        # There is no supported independent quota RPC for these installed CLIs.
        # A refresh reads the last captured native payload; it never automates /usage.
        runtime=verify_usage(journal,self.provider,executable(self.provider))
        if self.provider=='antigravity':
            from ..antigravity import catalog
            values=await catalog(executable(self.provider),timeout=5)
            return dict(kind='catalog',source='antigravity-models',source_version=runtime,
                models=[dict(model=k,display_name=v,capability_source='native-model-catalog',support='NATIVE_CATALOG',entitlement='UNVERIFIED') for k,v in list(values.items())[:64]])
        return None


COLLECTORS={'codex':CodexCollector(),'claude':TelemetryCollector('claude'),
            'antigravity':TelemetryCollector('antigravity')}
