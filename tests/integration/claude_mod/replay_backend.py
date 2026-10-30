"""A bridge backend that replays scripted activity over the real bridge protocol.

Harness-only. `nativepeers.bridge.Bridge` serves it exactly as it serves the
NativePeers backend, so the np mod is exercised end to end with no provider and
no model. Every request is logged to NP_REPLAY_LOG for assertions.

Scenario (NP_REPLAY_SCENARIO, JSON):
  {"providers": [...], "delay_ms": 40,
   "workers": {"codex": {"events": [...], "followup": [...], "model": "...", "effort": "..."}},
   "die_after_events": null, "refuse": {"claude": "QUOTA_EXHAUSTED"}}
Events are activity events without seq/worker ids; "generation" defaults to the
worker's current generation.
"""
from __future__ import annotations

import asyncio
import json
import os
import time

from nativepeers.errors import PeerError, require

SCHEMA = 'nativepeers.activity.v1'


class Worker:
    def __init__(self, worker_id, agent_id, provider, spec, script):
        self.id, self.agent, self.provider, self.spec, self.script = worker_id, agent_id, provider, spec, script
        self.group = 'G-' + worker_id;self.events = [];self.generation = 1;self.state = 'running'
        self.started = time.time();self.ended = None;self.task = f'T-{worker_id}-1';self.changed = asyncio.Event();self.planned = 1

    def append(self, kind, data, *, item=None, generation=None):
        event = dict(schema=SCHEMA, seq=len(self.events)+1, at=time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime()) + '.%03dZ' % (int(time.time() * 1000) % 1000), worker_id=self.id,
                     group_id=self.group, task_id=self.task, generation=generation or self.generation, native_turn=None, item=item,
                     kind=kind, data=data, source=dict(provider=self.provider, event='replay'))
        self.events.append(event);self.changed.set();self.changed = asyncio.Event()
        if kind == 'task.terminal':self.state = data.get('status', 'completed');self.ended = time.time()
        return event


class ReplayBackend:
    def __init__(self, scenario, log_path=None, session=None):
        self.scenario, self.log_path, self.session = scenario, log_path, session
        self.workers, self.bindings, self.requests, self.inputs = {}, {}, {}, {}
        self.owner = os.getppid();self.emitted = 0;self.lineage_version = 0;self.lineage_changed = asyncio.Event()
        self.die = asyncio.Event()

    def log(self, kind, **data):
        if self.log_path:
            with open(self.log_path, 'a') as stream:stream.write(json.dumps(dict(at=time.time(), kind=kind, **data))+'\n')

    def hello(self):
        return dict(peer='np1_replay', version='replay', providers=self.scenario.get('providers', ['codex', 'claude', 'antigravity', 'grok']),
                    capabilities=dict(steer=dict(codex=True), activity_schema=SCHEMA, backfill=['codex', 'claude']))

    async def owner_alive(self):
        if self.die.is_set():return False
        try:os.kill(self.owner, 0);return True
        except ProcessLookupError:return False

    async def renew_leases(self):pass
    def has_leases(self):return any(w.state == 'running' for w in self.workers.values())
    async def close(self, reason):self.log('close', reason=reason)

    async def catalog(self):
        return dict(providers={p: dict(models=[dict(id=self.scenario.get('workers', {}).get(p, {}).get('model', p+'-model'), efforts=['low', 'medium', 'high'])])
                               for p in self.hello()['providers']}, profiles=['analysis', 'workspace'], quota={})

    def touch(self):
        self.lineage_version += 1;self.lineage_changed.set();self.lineage_changed = asyncio.Event()

    def row(self, w):
        return dict(workerId=w.id, groupId=w.group, taskId=w.task, generation=w.generation, provider=w.provider, modelRequested=w.spec.get('model'),
                    modelObserved=w.script.get('model'), effort=w.spec.get('reasoning_effort'), profile=w.spec.get('permission_profile'),
                    title=w.spec.get('title', w.id), status=w.state if w.state != 'running' else 'running', startedAt=int(w.started*1000),
                    endedAt=int(w.ended*1000) if w.ended else None, agentId=w.agent, mirrorOwner='this' if w.agent else None, nativeId='native-'+w.id)

    def external(self):
        # A worker started outside the mod (npeer spawn): no agent until attached.
        spec = self.scenario.get('external')
        if not spec or getattr(self, 'external_made', False):return
        self.external_made = True
        worker_id = f'W{len(self.workers)+1}'
        w = Worker(worker_id, None, spec['provider'], dict(title=spec.get('title', 'adopted'), model=spec.get('model'), permission_profile='analysis'), spec)
        self.workers[worker_id] = w;self.touch();self.log('external', worker_id=worker_id)

    async def lineage(self, cursor, wait_ms):
        if self.scenario.get('external'):self.external()
        if cursor == str(self.lineage_version) and wait_ms:
            try:await asyncio.wait_for(self.lineage_changed.wait(), wait_ms/1000)
            except (TimeoutError, asyncio.TimeoutError):return dict(cursor=cursor, changed=False, workers=[self.row(w) for w in self.workers.values()])
        return dict(cursor=str(self.lineage_version), changed=True, workers=[self.row(w) for w in self.workers.values()])

    async def spawn(self, body):
        worker = body.get('worker') or {};provider = worker.get('provider')
        refuse = self.scenario.get('refuse', {}).get(provider)
        if body.get('check_only'):
            self.log('spawn.check', worker=worker)
            require(not refuse, refuse or 'OK', f'{provider} refused by the replay scenario')
            script = self.scenario.get('workers', {}).get(provider, {})
            return dict(ok=True, model=worker.get('model') or script.get('model'), effort=worker.get('reasoning_effort') or script.get('effort'))
        key = body['client_request_id']
        if key in self.requests:return self.requests[key]
        agent = body.get('claude_agent_id');worker_id = f'W{len(self.workers)+1}'
        w = Worker(worker_id, agent, provider, worker, self.scenario.get('workers', {}).get(provider, {}))
        self.workers[worker_id] = w;self.bindings[agent] = w;self.touch()
        self.log('spawn', worker_id=worker_id, agent=agent, worker=worker, mirror=body.get('mirror'))
        asyncio.get_running_loop().create_task(self.play(w, w.script.get('events', []), origin='parent-model'))
        self.requests[key] = dict(state='pending', worker_id=worker_id, group_id=w.group)
        return self.requests[key]

    async def play(self, w, events, *, origin):
        delay = self.scenario.get('delay_ms', 40)/1000
        w.append('task.started', dict(origin=origin, client_request_id=None, input_preview=''))
        for spec in events:
            await asyncio.sleep(spec.get('delay_ms', delay*1000)/1000)
            if w.state not in ('running',):return
            w.append(spec['kind'], spec.get('data', {}), item=spec.get('item'))
            self.emitted += 1
            if self.scenario.get('die_after_events') and self.emitted >= self.scenario['die_after_events']:
                self.log('die');self.die.set();return
        self.touch()

    async def binding(self, agent, wait_ms):
        end = time.time() + wait_ms/1000
        while agent not in self.bindings and time.time() < end:await asyncio.sleep(0.05)
        w = self.bindings.get(agent)
        if not w:return dict(state='pending')
        self.log('binding', agent=agent, worker_id=w.id)
        return dict(state='bound', worker_id=w.id, group_id=w.group, task_id=w.task, generation=w.generation, model=w.spec.get('model') or w.script.get('model'),
                    effort=w.spec.get('reasoning_effort') or w.script.get('effort'), profile=w.spec.get('permission_profile'))

    async def mirror_status(self):
        return dict(leases=[dict(worker_id=w.id, claude_agent_id=w.agent) for w in self.workers.values() if w.agent])

    async def mirror(self, op, body):
        self.log('mirror.'+op, body=body)
        if op == 'attach' and not body.get('check_only'):
            w = self.workers.get(body['worker_id']);require(w, 'PROTOCOL_ERROR', 'unknown worker')
            w.agent = body.get('claude_agent_id');self.bindings[w.agent] = w;self.touch()
            if not w.events:asyncio.get_running_loop().create_task(self.play(w, w.script.get('events', []), origin='controller'))
        return dict(op=op, route='MIRROR_DELIVERED' if op == 'ack' else 'MIRROR_SURFACING' if op == 'surfacing' else None)

    async def activity(self, worker_id, after, wait_ms, maximum, item):
        w = self.workers.get(worker_id);require(w, 'PROTOCOL_ERROR', 'unknown worker')
        def select():
            out = [e for e in w.events if e['seq'] > after and (item is None or e['item'] == item or e['kind'] == 'task.terminal')]
            return out[:maximum]
        found = select()
        if not found and wait_ms:
            try:await asyncio.wait_for(w.changed.wait(), wait_ms/1000)
            except (TimeoutError, asyncio.TimeoutError):pass
            found = select()
        self.log('activity', worker_id=worker_id, after=after, item=item, returned=len(found))
        return dict(events=found, last_seq=len(w.events))

    async def input(self, worker_id, body):
        key = body['client_request_id']
        if key in self.inputs:self.log('input.duplicate', key=key);return self.inputs[key]
        w = self.workers[worker_id];self.log('input', worker_id=worker_id, body=body)
        running = w.state == 'running'
        w.planned += 1;generation = w.planned
        disposition = 'queued' if running else 'resumed'
        w.append('input.queued', dict(client_request_id=key, origin=body.get('origin'), mode='queue' if running else 'resume', preview=body.get('text', '')[:80]))
        async def later():
            while w.state == 'running' or w.generation < generation - 1:await asyncio.sleep(0.05)
            w.generation = generation;w.state = 'running';w.task = f'T-{w.id}-{generation}';w.ended = None;self.touch()
            await self.play(w, w.script.get('followup', [{'kind': 'message.completed', 'data': {'text': 'follow-up done', 'is_final_candidate': True}},
                                                        {'kind': 'task.terminal', 'data': {'status': 'completed', 'final_text': 'follow-up done'}}]), origin=body.get('origin'))
        asyncio.get_running_loop().create_task(later())
        self.inputs[key] = dict(disposition=disposition, task_id=f'T-{w.id}-{generation}', generation=generation, delivery_owner=body.get('delivery_owner', 'mirror'))
        return self.inputs[key]

    async def interrupt(self, worker_id, body):
        w = self.workers[worker_id];self.log('interrupt', worker_id=worker_id, body=body)
        if w.state == 'running':w.append('task.terminal', dict(status='interrupted', final_text='(interrupted)'))
        return dict(state='INTERRUPT_REQUESTED')

    async def cancel_worker(self, worker_id, body):
        w = self.workers[worker_id];self.log('cancel', worker_id=worker_id, body=body)
        if w.state == 'running':w.append('task.terminal', dict(status='canceled', final_text='', error=dict(code='USER_CANCELED', message='canceled')))
        return dict(state='CANCEL_REQUESTED', task_id=w.task)

    async def cancel_group(self, group_id, body):
        self.log('cancel_group', group_id=group_id, body=body)
        for w in self.workers.values():
            if w.group == group_id and w.state == 'running':w.append('task.terminal', dict(status='canceled', final_text=''))
        return dict(state='CANCEL_REQUESTED')

    async def result(self, worker_id):
        w = self.workers[worker_id]
        return dict(model_observed=w.script.get('model'), native_id='native-'+w.id, elapsed_ms=int(((w.ended or time.time())-w.started)*1000),
                    result_artifact_id='A-'+w.task, changes=w.script.get('changes'))

    async def resume_command(self, worker_id):
        return dict(command=f'npeer resume-worker {worker_id} --task-file FILE')

    async def artifact(self, artifact_id, offset, limit):
        return dict(artifact_id=artifact_id, offset=offset, text='')
