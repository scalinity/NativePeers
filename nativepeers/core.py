"""Shared authenticated application operations for CLI and MCP."""
import json
from pathlib import Path

from .artifacts import Artifacts
from .control import group_call
from .delegation import Delegation
from .errors import require,PeerError
from .registry import Registry
from .routing import Router


class Core:
    def __init__(self,journal):
        self.journal=journal;self.registry=Registry(journal)
        self.router=Router(journal,self.registry)
        from .compatibility import verify
        from pathlib import Path
        for provider,kind in [('claude','claude-inbox'),('codex','codex-native-queue')]:
            adapter=self.router.adapters[kind]
            async def checked(peer,message,provider=provider,adapter=adapter):
                executable=Path.home()/('.local/bin/claude' if provider=='claude' else '.local/share/codex-mcp-manager/current/bin/codex')
                verify(journal,provider,executable)
                test=journal.root/'test-budget.json'
                if test.exists() and message['from']['kind']=='peer':
                    from .budget import LiveBudget
                    config=json.loads(test.read_text());budget=LiveBudget(Path(config['path']))
                    try:reservation=budget.reserve('peer native input '+message['message_id'],tranche=config.get('tranche','remaining_mandatory'))
                    except RuntimeError as error:raise PeerError('BUDGET_EXHAUSTED',str(error)) from error
                    budget.admit(reservation)
                    journal.event(message['message_id'],'live-peer-reservation',{'reservation':reservation,'native_id':peer['peer_key']['native_session_id']})
                return await adapter(peer,message)
            self.router.adapters[kind]=checked
        async def owned(peer,message):
            return await group_call(journal,peer['transport']['group_id'],'enqueue',{'worker_id':peer['transport']['worker_id'],'message':message})
        self.router.adapters['owned-worker']=owned
        self.delegation=Delegation(journal,self.registry)

    async def tool(self,caller,name,args):
        if name=='peer_list':return self.registry.list(args.get('provider'))
        if name=='peer_send':return await self.router.send(caller,args['target'],args['text'],expect_reply=args.get('expect_reply',False),client_request_id=args['client_request_id'])
        if name=='peer_reply':return await self.router.send(caller,'reply-author',args['text'],in_reply_to=args['message_id'],expect_reply=args.get('expect_reply',False),client_request_id=args['client_request_id'])
        if name=='peer_inbox':return self.router.inbox(caller,args.get('limit',20))
        if name=='worker_spawn':return await self.delegation.spawn(caller,args['spec'])
        if name=='task_status':return self.delegation.status(caller,args['handle'])
        if name=='task_result':return self.delegation.result(caller,args['handle'])
        if name=='task_wait':
            group=self.delegation.group(caller,args['handle'])
            require(not caller.grants.get('worker_id') or group['parent_key']==caller.key,'CAPACITY_UNAVAILABLE','A worker cannot wait for its own containing group to finish')
            return await self.delegation.wait(caller,args['handle'],args.get('timeout_ms',30000),args.get('on_timeout'))
        if name=='task_cancel':return await self.delegation.cancel(caller,args['handle'],args['reason'])
        if name=='artifact_read':
            service=Artifacts(self.journal)
            try:return service.read(args['handle'],caller.key,args.get('offset',0),args.get('limit',262144))
            except PeerError:
                require(caller.grants.get('worker_id'),'APPROVAL_BLOCKED','Artifact reader grant is absent')
                return service.read(args['handle'],caller.grants['worker_id'],args.get('offset',0),args.get('limit',262144))
        require(False,'PROTOCOL_ERROR','Unknown NativePeers operation')
