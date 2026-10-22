"""Bound official-SDK facade. Initialization never waits for a native ID."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from mcp.server import Server, NotificationOptions
from mcp.server.stdio import stdio_server
from mcp.types import Tool, ListToolsResult, CallToolResult, TextContent

from .errors import PeerError, require
from .journal import Journal
from .receipts import delivery_error
from .registry import Registry
from .caller import bind_caller, read_binding


def object_schema(properties, required=()):
    return dict(type='object',properties=properties,required=list(required),additionalProperties=False)


STRING={'type':'string'}
SPAWN_SCHEMA=json.loads((Path(__file__).resolve().parent.parent/'schemas/spawn.v1.json').read_text())
TOOLS={
 'peer_list':('Discover exact registered native titles.',object_schema({'provider':STRING})),
 'peer_send':('Send a peer notice or correlated request from this bound caller.',object_schema({'target':STRING,'text':STRING,'expect_reply':{'type':'boolean'},'client_request_id':STRING},['target','text','client_request_id'])),
 'peer_reply':('Reply to the immediate author of a received message.',object_schema({'message_id':STRING,'text':STRING,'expect_reply':{'type':'boolean'},'client_request_id':STRING},['message_id','text','client_request_id'])),
 'peer_inbox':('Read a bounded inbox without starting a model turn.',object_schema({'limit':{'type':'integer','minimum':1,'maximum':100},'unread':{'type':'boolean'},'paged':{'type':'boolean'},'cursor':STRING})),
 'peer_ack':('Record this recipient\'s model-reported processing acknowledgment; this is not native ingestion evidence.',object_schema({'message_id':STRING},['message_id'])),
 'worker_spawn':('Launch independent native workers, with a provider-native model ID or alias per worker. These are not vendor-native subagents.',object_schema({'spec':SPAWN_SCHEMA},['spec'])),
 'task_groups':('List this bound parent\'s retained delegations without claiming notifications.',object_schema({'limit':{'type':'integer','minimum':1,'maximum':100},'cursor':STRING})),
 'task_status':('Read task/group or exact-message status; refresh observes native receipts without replay.',object_schema({'handle':STRING,'refresh':{'type':'boolean'}})),
 'task_wait':('Wait at most 30 seconds through the current tool call.',object_schema({'handle':STRING,'timeout_ms':{'type':'integer','minimum':1,'maximum':30000},'on_timeout':{'enum':['push','retain']}},['handle'])),
 'task_result':('Read retained immutable attributed results.',object_schema({'handle':STRING},['handle'])),
 'task_cancel':('Cancel only work owned by this caller.',object_schema({'handle':STRING,'reason':STRING},['handle','reason'])),
 'artifact_read':('Read a registered artifact page within the caller grant.',object_schema({'handle':STRING,'offset':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':262144}},['handle'])),
}


from .protocol import valid


class Facade:
    def __init__(self,journal,binding):
        self.journal,self.binding=journal,binding
        self.sessions=set();self.watch_task=None

    def binding_state(self):
        if self.binding is None:
            try:
                caller=bind_caller(self.journal,Registry(self.journal),allow_external=False)
                return {'state':'BOUND','provider':'antigravity','grants':dict(caller.grants,can_spawn=caller.grants.get('can_spawn',not caller.grants.get('worker_id')))}
            except PeerError as error:
                bootstrap=os.getenv('NPEER_BOOTSTRAP')
                if error.code=='CALLER_NOT_READY' and bootstrap and not os.getenv('NPEER_BINDING'):
                    value=read_binding(self.journal,Path(bootstrap),allow_reserved=True)
                    # Inactive discovery/status may precede the native PID. No
                    # operation dispatches until a fresh bound caller is verified.
                    return dict(value,state='RESERVED_UNBOUND')
                if bootstrap or os.getenv('NPEER_BINDING'):raise
                return {'state':'RESERVED_UNBOUND','provider':'antigravity','grants':{'can_spawn':True}}
        return json.loads(self.binding.read_text())

    def names(self):
        state=self.binding_state()
        if state.get('state')!='BOUND' and state.get('provider') not in ('claude','antigravity'):return ['task_status']
        granted=state.get('grants',{})
        names=['peer_list','peer_send','peer_reply','peer_inbox','peer_ack','task_groups','task_status','task_wait','task_result','artifact_read']
        if granted.get('can_spawn'):names += ['worker_spawn','task_cancel']
        return names

    async def watch(self):
        prior=self.names()
        while True:
            await asyncio.sleep(.1)
            current=self.names()
            if current!=prior:
                for session in list(self.sessions):
                    try:await session.send_tool_list_changed()
                    except Exception:self.sessions.discard(session)
                prior=current

    async def list_tools(self,ctx,params):
        self.sessions.add(ctx.session)
        if self.watch_task is None:self.watch_task=asyncio.create_task(self.watch())
        return ListToolsResult(tools=[Tool(name=n,description=TOOLS[n][0],input_schema=TOOLS[n][1]) for n in self.names()])

    async def call(self,ctx,params):
        try:
            require(params.name in TOOLS,'PROTOCOL_ERROR','Unknown namespaced tool')
            args=params.arguments or {};schema=TOOLS[params.name][1]
            require(valid(args,schema),'PROTOCOL_ERROR','Invalid tool arguments')
            state=self.binding_state()
            if state.get('state')!='BOUND':
                require(params.name=='task_status','CALLER_NOT_READY','Native identity is reserved but not bound')
                value={'state':'RESERVED_UNBOUND','worker_id':state.get('worker_id')}
            else:
                require(params.name in self.names(),'APPROVAL_BLOCKED','Tool is outside the fixed lease')
                from .core import Core
                caller=bind_caller(self.journal,Registry(self.journal),fixed_binding=self.binding,allow_external=False)
                if params.name=='task_status' and not args.get('handle'):
                    peer=Registry(self.journal).verify(caller.key,caller.incarnation)
                    value={'state':'BOUND','peer_key':caller.key,'native_session_id':peer['peer_key']['native_session_id'],'title':peer['title'],'worker_id':caller.grants.get('worker_id')}
                else:value=await Core(self.journal).tool(caller,params.name,args)
            error = delivery_error(value) if params.name in ('peer_send','peer_reply') else None
            response=dict(schema_version=1,ok=error is None,data=value,error=error.json() if error else None)
        except PeerError as error:
            response=dict(schema_version=1,ok=False,data=None,error=error.json())
        return CallToolResult(content=[TextContent(type='text',text=json.dumps(response))],is_error=not response['ok'])


async def serve(binding):
    journal=Journal();facade=Facade(journal,binding)
    server=Server('nativepeers',version='0.1.0',on_list_tools=facade.list_tools,on_call_tool=facade.call)
    try:
        async with stdio_server() as (reader,writer):
            await server.run(reader,writer,server.create_initialization_options(notification_options=NotificationOptions(tools_changed=True)))
    finally:
        if facade.watch_task:facade.watch_task.cancel()
        journal.close()


def main():
    parser=argparse.ArgumentParser();mode=parser.add_mutually_exclusive_group(required=True);mode.add_argument('--binding',type=Path);mode.add_argument('--native-caller',action='store_true');args=parser.parse_args();asyncio.run(serve(args.binding))


if __name__=='__main__':main()
