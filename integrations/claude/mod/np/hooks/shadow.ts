// The shadow engine: registry, step answering, the mirror tool's wait, input
// capture and completion. Every function that takes $ is a top-level
// declaration (the validator refuses $ handed to a closure, X12).
import type { EngineInterface, TurnStepInput, TurnStepResult } from 'claude-code'
import type { NpInput, NpProvider, NpShadow } from '../types'
import { BridgeError, request, sleep } from './bridge.ts'
import { SENTINEL, shadowModel, TAGS } from './agents.ts'
import { followUp, isBrief, requestId, unforwarded } from './inputs.ts'
import { MIRROR_TOOL, finalAnswer, openStep, planBatch, toolResult } from './mirror.ts'
import { NONE, getIndex, getMember, setFaults, setMember } from './state.ts'
import type { Activity, Summary } from './mirror.ts'

const STEP_CAP_MS = 8 * 60 * 1000
const QUIET_MS = 60 * 1000
const STORE_LIMIT = 200

export async function loadShadow($: EngineInterface, agentId: string | undefined): Promise<NpShadow | undefined> {
  if (!agentId) return undefined
  const held = await getMember($, agentId)
  if (held.agentId === agentId) return held
  // $.state is per session id: after /clear or --resume it reads empty, and the
  // durable copy re-attaches the shadow (gate G10).
  const stored = await $.store.get('shadow:' + agentId) as NpShadow | undefined
  if (stored && stored.agentId === agentId) {
    await setMember($, stored)
    return stored
  }
  return undefined
}

export async function saveShadow($: EngineInterface, shadow: NpShadow): Promise<void> {
  const s = { ...shadow, inputs: shadow.inputs.slice(-50) }
  await setMember($, s)
  await $.store.set('shadow:' + s.agentId, s)
}

// Keeps the durable copies to the newest 200 and to 30 days.
export async function pruneStore($: EngineInterface): Promise<void> {
  const keys = (await $.store.keys()).filter(k => k.startsWith('shadow:'))
  const now = await $.clock.now(), rows: { key: string; at: number }[] = []
  for (const key of keys) rows.push({ key, at: Number(((await $.store.get(key)) as NpShadow | undefined)?.startedAt ?? 0) })
  rows.sort((a, b) => b.at - a.at)
  for (const [n, row] of rows.entries())
    if (n >= STORE_LIMIT || now - row.at > 30 * 86400000) await $.store.delete(row.key)
}

export function newShadow(agentId: string, provider: NpProvider, title: string, session: string, startedAt: number, extra: Partial<NpShadow> = {}): NpShadow {
  return { ...NONE, agentId, provider, title, session, startedAt, phase: 'BINDING', briefConsumed: true, ...extra }
}

function toolId(): string {
  return 'toolu_01np' + Math.random().toString(36).slice(2, 12) + Math.random().toString(36).slice(2, 12)
}

function result(e: TurnStepInput, answer: string, stopReason: 'end_turn' | 'tool_use', toolUses: { name: string; input: unknown }[], outputTokens: number, provider: NpProvider): TurnStepResult {
  const usage = outputTokens > 0 ? { input_tokens: 0, output_tokens: outputTokens, cache_read_input_tokens: 0, cache_creation_input_tokens: 0, model: shadowModel(provider) } : null
  return { turnId: e.turnId, index: e.index, answer, toolUses, stopReason, usage }
}

// Transport errors are retried with backoff; after five the step fails closed
// through the registration's .catch.
async function poll($: EngineInterface, path: string): Promise<any> {
  const backoff = [500, 1000, 2000, 4000, 8000]
  for (let attempt = 0;;attempt++) {
    try { return await request($, 'GET', path) } catch (error) {
      if (!(error instanceof BridgeError) || !['BRIDGE_UNREACHABLE', 'BRIDGE_DOWN'].includes(error.code) || attempt >= backoff.length) throw error
      await sleep($, backoff[attempt] ?? 8000)
    }
  }
}

async function bind($: EngineInterface, s: NpShadow): Promise<string | undefined> {
  while (!s.workerId) {
    const b = await poll($, `/v1/bindings/${s.agentId}?wait_ms=25000`)
    if (b.state === 'bound') {
      Object.assign(s, { workerId: b.worker_id, groupId: b.group_id, taskId: b.task_id, generation: b.generation, model: b.model ?? s.model,
        effort: b.effort ?? s.effort, profile: b.profile ?? s.profile, phase: 'STREAMING' })
      await saveShadow($, s)
    } else if (b.state === 'failed') {
      return `NativePeers refused this worker: ${b.error?.code ?? 'SPAWN_FAILED'} ${String(b.error?.message ?? '').slice(0, 400)}`
    }
  }
  return undefined
}

async function forward($: EngineInterface, s: NpShadow, input: NpInput, mode: 'queue' | 'steer'): Promise<string> {
  input.attempts += 1
  try {
    const r = await request($, 'POST', `/v1/workers/${s.workerId}/input`, { client_request_id: requestId(input.uuid), text: input.text, origin: input.origin, mode, delivery_owner: 'mirror' })
    Object.assign(input, { forwarded: true, disposition: r.disposition, reason: r.reason, generation: r.generation })
  } catch (error) {
    const code = error instanceof BridgeError ? error.code : 'TRANSPORT'
    if (input.attempts >= 3 || !['BRIDGE_UNREACHABLE', 'BRIDGE_DOWN'].includes(code)) Object.assign(input, { forwarded: true, disposition: 'refused', reason: code })
  }
  await saveShadow($, s)
  if (input.disposition === 'refused') return `↳ follow-up not delivered: ${input.reason ?? 'refused'}\n`
  if (input.disposition === 'resumed') return '↳ resuming the worker with your follow-up\n'
  if (input.disposition === 'steered') return '↳ follow-up steered into the running turn\n'
  if (input.disposition === 'queued') return input.reason === 'steer-unsupported' ? '↳ queued for the worker (steering unsupported)\n' : '↳ queued for the worker (after its current turn)\n'
  return ''
}

async function summary($: EngineInterface, s: NpShadow, terminal: Activity): Promise<Summary> {
  const base: Summary = { provider: s.provider, modelRequested: s.model, effort: s.effort, profile: s.profile, workerId: s.workerId ?? '?',
    groupId: s.groupId, taskId: terminal.task_id ?? s.taskId, elapsedMs: (await $.clock.now()) - s.startedAt, resultArtifact: terminal.data?.result_artifact_id }
  try {
    const r = await request($, 'GET', `/v1/workers/${s.workerId}/result`)
    return { ...base, modelObserved: r.model_observed, nativeId: r.native_id, elapsedMs: r.elapsed_ms ?? base.elapsedMs,
      resultArtifact: r.result_artifact_id ?? base.resultArtifact, patchArtifact: r.patch_artifact, changes: r.changes }
  } catch {
    return base
  }
}

export type StepOptions = { showReasoning: boolean; followupMode: 'queue' | 'steer-if-supported'; steer: boolean }

// One model step of a shadow, answered from the worker's activity. Every path
// returns a complete TurnStepResult: a bare return makes the hook absent and
// the engine would send a real request (X10).
export async function* stepShadow($: EngineInterface, e: TurnStepInput, s: NpShadow, o: StepOptions): AsyncGenerator<any, TurnStepResult> {
  const open = openStep()
  let usage = 0
  const say = (text: string) => { const chunk = { kind: 'text' as const, index: open.block++, text };open.answer += text;return chunk }
  if (s.spawnError || (!s.workerId && s.phase === 'FAULTED')) {
    yield say(`NativePeers could not start this worker: ${s.spawnError ?? s.lastFault ?? 'unknown'}`)
    yield { kind: 'stop', stopReason: 'end_turn', usage: null }
    s.phase = 'COMPLETED';await saveShadow($, s)
    return result(e, open.answer, 'end_turn', [], 0, s.provider)
  }
  const failure = await bind($, s)
  if (failure) {
    yield say(failure);yield { kind: 'stop', stopReason: 'end_turn', usage: null }
    s.phase = 'COMPLETED';await saveShadow($, s)
    return result(e, open.answer, 'end_turn', [], 0, s.provider)
  }
  if (s.inflight) {
    yield say(`[mirror resumed after reload; up to ${s.inflight.to - s.inflight.from} activity events may be missing here — the native history is complete]\n`)
    s.inflight = undefined
  }
  for (const input of unforwarded(s.inputs)) {
    const note = await forward($, s, input, o.followupMode === 'steer-if-supported' && o.steer ? 'steer' : 'queue')
    if (note) yield say(note)
  }
  const ctx = { workerId: s.workerId!, showReasoning: o.showReasoning, newId: toolId,
    continuesAfter: (g: number) => s.inputs.some(i => i.forwarded && i.generation !== undefined && i.generation > g && !s.terminals.includes(i.generation)) }
  const opened = await $.clock.now()
  let quietSince = opened, lastBeat = opened
  while (true) {
    const batch = await poll($, `/v1/workers/${s.workerId}/activity?after=${s.cursor}&wait_ms=25000&max=256`)
    const events: Activity[] = Array.isArray(batch.events) ? batch.events : []
    const plan = planBatch(events, open, ctx, s.usageCumulative)
    if (plan.consumed !== null) {
      for (const ev of events) {
        if (ev.seq > plan.consumed) break
        if (ev.kind === 'task.started') Object.assign(s, { generation: ev.generation, taskId: ev.task_id ?? s.taskId })
        if (ev.kind === 'task.terminal' && !s.terminals.includes(ev.generation)) s.terminals.push(ev.generation)
        if (ev.kind === 'tool.started') s.latest = String(ev.data?.title ?? ev.data?.kind ?? '').slice(0, 120)
      }
      // Commit before yielding: a reload resumes from here and marks what may be lost.
      s.inflight = { from: s.cursor, to: plan.consumed };s.cursor = plan.consumed
      s.pendingRefs = { ...s.pendingRefs, ...plan.refs }
      if (plan.usage.cumulative !== undefined) s.usageCumulative = plan.usage.cumulative
      await saveShadow($, s)
      quietSince = await $.clock.now()
    }
    usage += plan.usage.delta
    for (const chunk of plan.chunks) yield chunk
    if (plan.end === 'tool_use') {
      Object.assign(s, { phase: 'TOOL_PENDING', inflight: undefined });await saveShadow($, s)
      yield { kind: 'stop', stopReason: 'tool_use', usage: null }
      return result(e, open.answer, 'tool_use', plan.toolUses, usage, s.provider)
    }
    if (plan.end === 'terminal' && plan.terminal) {
      const t = plan.terminal
      const owner = t.generation === s.nativeGeneration ? 'native' : 'mirror'
      if (owner === 'mirror') {
        try { await request($, 'POST', '/v1/mirror/surfacing', { worker_id: s.workerId, task_id: t.task_id, generation: t.generation }) } catch { /* the route stays pending; the lease decides */ }
        s.surfaced = { generation: t.generation, taskId: t.task_id ?? undefined }
      }
      const text = finalAnswer(t, await summary($, s, t), owner, s.terminals)
      yield say((open.answer && !open.answer.endsWith('\n') ? '\n\n' : '') + text)
      Object.assign(s, { phase: 'COMPLETING', inflight: undefined });await saveShadow($, s)
      yield { kind: 'stop', stopReason: 'end_turn', usage: usage > 0 ? { input_tokens: 0, output_tokens: usage, cache_read_input_tokens: 0, cache_creation_input_tokens: 0, model: shadowModel(s.provider) } : null }
      return result(e, open.answer, 'end_turn', [], usage, s.provider)
    }
    const now = await $.clock.now()
    // A single step is aborted by the engine after 600 s (gate G8): past eight
    // quiet minutes the step ends on a 'waiting' mirror call, which may hold.
    if (now - opened > STEP_CAP_MS && open.tools === 0) {
      s.waits += 1
      const r = `${s.workerId}:${s.generation}:wait${s.waits}`
      const input = { kind: 'wait', title: `${s.provider} is still working`, detail: '', ref: r }
      s.pendingRefs = { ...s.pendingRefs, [r]: { item: 'wait' + s.waits, kind: 'wait', startSeq: s.cursor, title: input.title, detail: '', generation: s.generation } }
      Object.assign(s, { phase: 'TOOL_PENDING', inflight: undefined });await saveShadow($, s)
      const index = open.block++, id = toolId()
      yield { kind: 'tool', index, id, name: MIRROR_TOOL }
      yield { kind: 'input', index, json: JSON.stringify(input) }
      yield { kind: 'stop', stopReason: 'tool_use', usage: null }
      return result(e, open.answer, 'tool_use', [{ name: MIRROR_TOOL, input }], usage, s.provider)
    }
    if (o.showReasoning && now - quietSince > QUIET_MS && now - lastBeat > QUIET_MS) {
      lastBeat = now
      yield { kind: 'thinking', index: open.block++, text: `${s.provider}: still working (${Math.round((now - s.startedAt) / 1000)}s)` }
    }
  }
}

// The mirror tool: never executes anything; it waits for the recorded result
// of the worker's own tool call and replays it.
export async function mirrorCall($: EngineInterface, agentId: string | undefined, input: any, limit: number, signal: AbortSignal): Promise<{ result: string } | { deny: string }> {
  const s = await loadShadow($, agentId)
  const pending = s?.pendingRefs?.[String(input?.ref ?? '')]
  if (!s || !pending || pending.kind !== input?.kind || pending.title !== input?.title || pending.detail !== (input?.detail ?? ''))
    return { deny: 'Internal NativePeers display tool: calls outside a NativePeers shadow are refused.' }
  let text: string
  if (pending.kind === 'wait') {
    while (!signal.aborted) {
      const batch = await poll($, `/v1/workers/${s.workerId}/activity?after=${s.cursor}&wait_ms=20000&max=1`)
      if (batch.events?.length) break
    }
    text = `[${s.provider}] activity resumed`
  } else if (pending.immediate) {
    const batch = await poll($, `/v1/workers/${s.workerId}/activity?after=${pending.startSeq - 1}&max=1`)
    text = toolResult(s.provider, pending, batch.events ?? [], limit).text
  } else {
    let events: Activity[] = [], after = pending.startSeq
    while (!signal.aborted) {
      const batch = await poll($, `/v1/workers/${s.workerId}/activity?after=${after}&item=${encodeURIComponent(pending.item)}&wait_ms=20000&max=64`)
      const fresh: Activity[] = batch.events ?? []
      events = events.concat(fresh)
      for (const ev of fresh) after = Math.max(after, ev.seq)
      if (events.some(ev => ev.kind === 'tool.completed' || (ev.kind === 'task.terminal' && ev.generation === pending.generation))) break
    }
    text = toolResult(s.provider, pending, events, limit).text
  }
  const fresh = await loadShadow($, agentId)
  if (fresh) {
    const { [String(input.ref)]: _done, ...rest } = fresh.pendingRefs
    fresh.pendingRefs = rest;if (!Object.keys(rest).length) fresh.phase = 'STREAMING'
    await saveShadow($, fresh)
  }
  return { result: signal.aborted ? `[${s.provider} ${pending.kind}] interrupted` : text }
}

// session.append for a shadow: record a follow-up row once, by its uuid.
export async function captureRow($: EngineInterface, agentId: string | undefined, row: { door: string; origin: any; message: any }, uuid: string): Promise<void> {
  const s = await loadShadow($, agentId)
  if (!s) return
  const found = followUp(row)
  if (!found) return
  if (!s.briefConsumed && isBrief(found.text, s.brief)) { s.briefConsumed = true;await saveShadow($, s);return }
  if (isBrief(found.text, s.brief) && found.origin === 'parent-model' && row.door === 'prompt' && !s.inputs.length && s.phase === 'BINDING') return
  if (s.inputs.some(i => i.uuid === uuid)) return
  s.inputs.push({ uuid, text: found.text, origin: found.origin, forwarded: false, attempts: 0 })
  await saveShadow($, s)
}

// turn.complete for a shadow: acknowledge a surfaced answer, map a stop to the
// worker, and detect a fallthrough (a real model request for a shadow).
export async function completeShadow($: EngineInterface, agentId: string, reason: string | undefined, isAborted: boolean, answer: string, usage: any): Promise<void> {
  const s = await loadShadow($, agentId)
  if (!s) return
  const fell = (usage && (Number(usage.input_tokens) > 0 || Number(usage.cache_creation_input_tokens) > 0)) || answer.includes(SENTINEL) || reason === 'error'
  if (fell) {
    s.fallthrough = true
    await setFaults($, { fallthrough: true, reason: `shadow ${agentId}: ${reason ?? 'model output'}`, at: Date.now() })
    $.ui.toast(`np: a shadow made a model request (${TAGS[s.provider]}). NativePeers agent types are off until /np doctor passes.`)
    try { await request($, 'POST', '/v1/mirror/fallthrough', { claude_agent_id: agentId, worker_id: s.workerId, reason: reason ?? 'unknown' }) } catch { /* recorded in state */ }
  }
  if (isAborted) {
    const status = (await $.agent.list()).find(a => a.id === agentId)?.status
    // Esc in the view, 'x' in the tasks list and TaskStop all end as 'killed'
    // and cannot be told apart (gate G4): each stops the worker's current task.
    if (status === 'killed' && s.workerId) {
      try { await request($, 'POST', `/v1/workers/${s.workerId}/cancel`, { client_request_id: `np-kill:${agentId}:${s.generation}`, reason: 'Stopped from Claude Code' }) } catch { /* the bridge records nothing then */ }
      try { await request($, 'POST', '/v1/mirror/ack', { worker_id: s.workerId, task_id: s.taskId, generation: s.generation, outcome: 'killed' }) } catch { /* lease fallback applies */ }
    }
    s.phase = 'COMPLETED'
  } else if (s.surfaced) {
    try { await request($, 'POST', '/v1/mirror/ack', { worker_id: s.workerId, task_id: s.surfaced.taskId, generation: s.surfaced.generation }) } catch { /* NOTIFICATION_UNKNOWN on lease expiry */ }
    s.surfaced = undefined;s.phase = 'COMPLETED'
  } else if (s.phase === 'COMPLETING') {
    s.phase = 'COMPLETED'
  }
  await saveShadow($, s)
}

export async function faultShadow($: EngineInterface, agentId: string | undefined, kind: string): Promise<void> {
  const s = await loadShadow($, agentId)
  if (!s) return
  Object.assign(s, { phase: 'FAULTED', lastFault: kind });await saveShadow($, s)
  try { await request($, 'POST', '/v1/mirror/fault', { claude_agent_id: s.agentId, worker_id: s.workerId, kind, message: `turn.step ${kind}` }) } catch { /* fault already shown */ }
}

export async function isShadowStep($: EngineInterface, agentId: string | undefined): Promise<NpShadow | undefined> {
  if (agentId === undefined) return undefined
  return loadShadow($, agentId)
}

export async function shadows($: EngineInterface): Promise<NpShadow[]> {
  const out: NpShadow[] = []
  for (const id of await getIndex($)) { const s = await loadShadow($, id);if (s) out.push(s) }
  return out
}
