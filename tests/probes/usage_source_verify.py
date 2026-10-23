"""Fixed no-inference usage inspection/collection actions, never arbitrary prompts."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

from nativepeers.errors import PeerError,require
from nativepeers.journal import Journal
from nativepeers.launch import provider_home
from nativepeers.usage.collectors import COLLECTORS,executable
from nativepeers.usage.contracts import inspect,record,verified


async def execute(args,journal):
    binary=executable(args.provider).resolve(strict=True);home=provider_home(args.provider)
    if args.action=='inspect':
        if args.provider=='codex':
            from nativepeers.compatibility import verify
            return dict(status='PASS',runtime=verify(journal,'codex',binary),route='native-quota-rpc',model_inputs=0)
        result=inspect(args.provider,binary,authorize_pty=args.authorize_claude_pty)
        if args.record:record(journal,result)
        return dict(status='PASS' if result['qualified'] else 'BLOCKED',**result,model_inputs=0)
    if args.command=='credits':
        require(args.provider=='antigravity','PROTOCOL_ERROR','Only native Antigravity credits are inspected')
        from nativepeers.usage.native_command import run_native
        from nativepeers.usage.antigravity_native import envelope,credits
        receipt=verified(journal,args.provider,binary)
        result=await run_native('antigravity',binary,['-p','/credits','--output-format','json','--print-timeout','15s'],home,timeout=20)
        _,data=envelope(result['stdout'],'credits')
        credits(result['stdout'])
        if args.record:record(journal,dict(receipt,credits_contract='remaining_credits-v1'))
        if args.private_directory:
            from nativepeers.files import private_directory
            path=private_directory(args.private_directory)/'credits-report.json';path.write_bytes(result['stdout']);path.chmod(0o600)
        return dict(status='PASS',scope='Native credits envelope only; decoder/units must be separately qualified',schema_keys=sorted(data),cleanup=result['cleanup'],model_inputs=0)
    if args.action=='compare':
        from nativepeers.caller import bind_caller
        from nativepeers.registry import Registry
        from nativepeers.usage.service import Usage
        from mcp import ClientSession,StdioServerParameters
        from mcp.client.stdio import stdio_client
        caller=bind_caller(journal,Registry(journal),allow_external=False)
        current=await Usage(journal).status(caller,provider=args.provider,refresh=True)
        # The MCP SDK defaults omit native thread/binding variables. Forward
        # only the actual caller context; never synthesize a binding or UUID.
        from nativepeers.launch import launch_environment
        environment=launch_environment(args.provider)
        environment.update({key:os.environ[key] for key in ('CODEX_THREAD_ID','NPEER_HOME','NPEER_BINDING','NPEER_BOOTSTRAP','CODEX_HOME','CLAUDE_CONFIG_DIR') if key in os.environ})
        parameters=StdioServerParameters(command=sys.executable,args=['-m','nativepeers.mcp_server','--native-caller'],cwd=str(ROOT),env=environment)
        async with stdio_client(parameters) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                response=await session.call_tool('usage_status',{'provider':args.provider})
                envelope=json.loads(''.join(c.text for c in response.content if c.type=='text'))
                if response.is_error or not envelope.get('ok'):
                    return dict(status='BLOCKED',scope='Native stdio MCP caller gate; no binding was manufactured',
                        error=envelope.get('error'),model_inputs=0)
                same=not response.is_error and envelope.get('ok') and current['providers'][0]['observation_id']==envelope['data']['providers'][0]['observation_id']
        return dict(status='PASS' if same else 'FAIL',scope='Real native caller and stdio MCP transport; no model/client tool turn',model_inputs=0,collector_state=current['providers'][0]['collector_state'])
    options={'capture_directory':args.private_directory} if args.provider=='claude' else {}
    result=await COLLECTORS[args.provider].collect(journal,home,**options)
    return dict(status='PASS',data=result,model_inputs=0,scope='Actual native metadata read; quota outcome and freshness remain explicit')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['inspect','collect','compare']);parser.add_argument('--provider',choices=['codex','claude','antigravity'],required=True)
    parser.add_argument('--deadline-ms',type=int,default=20000);parser.add_argument('--json',action='store_true')
    parser.add_argument('--record',action='store_true');parser.add_argument('--authorize-claude-pty',action='store_true',help='Use only after explicit approval of the bounded usage-only PTY exception')
    parser.add_argument('--route',choices=['native-positional-usage','native-pty-usage']);parser.add_argument('--command',choices=['usage','credits'],default='usage')
    parser.add_argument('--private-directory',type=Path);args=parser.parse_args()
    require(1000<=args.deadline_ms<=30000,'PROTOCOL_ERROR','Choose a bounded 1–30 second metadata deadline')
    require(not args.authorize_claude_pty or args.action=='inspect' and args.provider=='claude','PROTOCOL_ERROR','PTY approval applies only to Claude inspection')
    journal=Journal()
    async def bounded():
        async with asyncio.timeout(args.deadline_ms/1000):
            if args.route=='native-positional-usage' and args.action!='inspect':
                raise PeerError('SOURCE_NOT_QUALIFIED','Installed Claude positional usage loses native cache-status provenance; it is not executed')
            return await execute(args,journal)
    try:
        try:value=asyncio.run(bounded())
        except PeerError as error:value={'status':'BLOCKED','error':error.json(),'model_inputs':0}
        except TimeoutError:value={'status':'BLOCKED','error':{'code':'SOURCE_TIMEOUT','message':'Bounded native metadata read timed out'},'model_inputs':0}
        print(json.dumps(value,indent=2))
    finally:journal.close()


if __name__=='__main__':main()
