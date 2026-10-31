// Wiring only: each on(...) hands its work to a top-level function of this
// mod's own files, the only places $ may be passed (the validator, X12).
import type { EngineInterface, Register, TurnStepInput, TurnStepResult } from 'claude-code'
import type { NpProvider, NpWorker } from '../types'
import { PROVIDERS, adoptionBrief, agentSpec, describe, isProvider, parseHeader, shadowModel, titleFrom, workerSpec } from './agents'
import { BridgeError, connected, connectionGeneration, followLineage, heartbeat, launch, request, restoreReady, shutdown, sleep, starting } from './bridge'
import { HANDBACK_TOOL } from './inputs'
import { MIRROR_TOOL } from './mirror'
import { captureRow, completeShadow, faultShadow, loadShadow, mirrorCall, newShadow, pruneStore, saveShadow, stepShadow } from './shadow'
import { getBridge, getFaults, getLineage, setBridge, setFaults, setLineage } from './state'
import { PANE_ID, copyResume, doctorText, interruptWorker, openConversation, openPane, openView, paneBeside, renderBand, renderPane, renderSpinner, renderToolUse, renderUserMessage, statusLine, statusText, stopAll, stopWorker } from './ui'

const MIN_ENGINE = [2, 1, 309]
const CATALOG = 'mcp__np__catalog'
const COMMAND = { name: 'np', description: 'NativePeers lineage: status, pane, open, stop, interrupt, attach, reattach, doctor, resume-cmd', argumentHint: '[pane|open N|stop N|stop all|interrupt N|attach N|all|reattach|doctor|resume-cmd N]', immediate: true as const }
const MODEL_PREFIX = 'np-mirror-'
const SECTION = [
  'NativePeers agent types (np:codex, np:claude, np:antigravity, np:grok) delegate to independent native CLI workers whose live transcripts appear in the tasks list.',
  'Start the Agent prompt with an optional header: ```np {"model":"...","effort":"...","profile":"analysis|workspace","title":"..."}``` then a newline and the brief.',
  'Call mcp__np__catalog once before choosing exact models and efforts; never guess a model id.',
  'Briefs must be self-contained: the worker sees only the brief, not this conversation.',
  'Workers run in the background: for fan-out, issue the Agent calls together, end your turn, and let the notifications arrive; do not poll.',
  'Follow up with SendMessage to the agent; results name the NativePeers group for `npeer result`.',
].join('\n')

type Options = { followupMode?: string; showReasoning?: string; mirrorOutputBytes?: number; band?: string; adoption?: string; npeerPath?: string; allowFromClaudeSubagents?: boolean; notifyOnCompletion?: string }

let activated = false
let inert = false
let rebind = false
let place: { cwd: string; npeer: string } | undefined
let timers = false
const offered = new Set<string>()

function atLeast(version: string, minimum: number[]): boolean {
  const parts = version.split(/[.+-]/).map(Number)
  for (let n = 0;n < minimum.length;n++) { const have = parts[n] ?? 0, need = minimum[n] ?? 0;if (have > need) return true;if (have < need) return false }
  return true
}

async function workerMode($: EngineInterface): Promise<boolean> {
  return Boolean(await $.env.get('NPEER_BOOTSTRAP'))
}

async function activate($: EngineInterface, cwd: string, o: Options): Promise<void> {
  if (activated || inert) return
  activated = true
  const version = (await $.session.version()).version
  if (!atLeast(version, MIN_ENGINE) || !version.startsWith('2.')) {
    await setBridge($, { state: 'degraded', reason: `Claude Code ${version} is outside the verified range (minimum 2.1.309)`, since: 0 })
    return
  }
  place = { cwd, npeer: o.npeerPath || '/Users/danny/.local/bin/npeer' }
  const launched = await startBridge($)
  if (launched && 'reason' in launched) { $.ui.toast(`NativePeers bridge unavailable: ${launched.reason}. Run /np doctor.`);return }
  if (launched) { await ready($, launched.hello);void followLineage($) }
  await pruneStore($)
}

// The one bridge start. launch() takes its slot before any await; a caller that finds a start in flight
// waits for it through $ sleeps, and undefined means that start connected and registered the session.
async function startBridge($: EngineInterface): Promise<{ hello: any } | { reason: string } | undefined> {
  const launched = await launch($, $.session.id(), place!.cwd, place!.npeer)
  if (!('busy' in launched)) return launched
  for (let n = 0;starting() && n < 60;n++) await sleep($, 250)
  return connected() ? undefined : { reason: starting() ? 'a bridge start is still in progress' : 'the bridge start in progress failed; run /np doctor' }
}

// Runs on every bridge start: the first registers the tools and timers, and each
// registers the agent types its bridge newly lists, so a bridge that was down when
// the session began, or one that now lists a renewed provider, offers them.
async function ready($: EngineInterface, hello: any): Promise<void> {
  const usable: string[] = Array.isArray(hello.providers) ? hello.providers : [...PROVIDERS]
  for (const provider of PROVIDERS) if (usable.includes(provider) && !offered.has(provider)) { await $.agent.register(agentSpec(provider));offered.add(provider) }
  if (timers) return
  timers = true
  await $.tool.register({ name: 'mirror', description: 'Internal: renders a NativePeers worker\'s activity. Calls outside a NativePeers shadow are refused.', isDeferred: true,
    inputSchema: { type: 'object', properties: { kind: { type: 'string' }, title: { type: 'string' }, detail: { type: 'string' }, ref: { type: 'string' } }, required: ['kind', 'title', 'ref'] } })
  await $.tool.register({ name: 'catalog', description: 'Read-only NativePeers catalog: providers, exact model ids with their efforts, the permission profiles this session may use, and a quota summary. Call once before choosing np:* models.', isDeferred: true,
    inputSchema: { type: 'object', properties: { provider: { type: 'string', enum: [...PROVIDERS] } } } })
  $.clock.every(15000, () => heartbeat($))
  $.clock.every(5000, () => statusLine($))
}

async function pathA($: EngineInterface, e: any, next: (e: any) => Promise<any>, o: Options): Promise<any> {
  if (e.resume) {
    const r = await next(e)
    if (r.agentId) await loadShadow($, r.agentId)
    return r
  }
  if (e.isTeammate || e.workflow) return { deny: 'np agents are not available in teams or workflows (v1)' }
  if (e.parentAgentId && !o.allowFromClaudeSubagents) return { deny: 'np agents can be started only from the main conversation' }
  const provider = String(e.subagentType).slice(3)
  if (!isProvider(provider)) return { deny: `unknown NativePeers provider "${provider}"` }
  if ((await getBridge($)).state !== 'ready') return { deny: 'NativePeers bridge unavailable — run /np doctor' }
  if ((await getFaults($)).fallthrough) return { deny: 'NativePeers agent types are off after a detected fallthrough — run /np doctor' }
  const parsed = parseHeader(String(e.prompt ?? ''))
  if ('error' in parsed) return { deny: `${parsed.error}; ${CATALOG} lists providers, models and efforts` }
  const title = titleFrom(parsed.header, e.description, parsed.brief)
  // A worker runs in the session's directory; a cwd travels only when the Agent call named one.
  const worker = workerSpec(provider, parsed.header, parsed.brief, title, e.cwd)
  if (rebind) await rebindSession($)
  let check: any
  try { check = await request($, 'POST', '/v1/spawn', { check_only: true, worker }) }
  catch (error) { return { deny: `${error instanceof BridgeError ? error.code : 'BRIDGE_ERROR'}: ${String((error as Error).message).slice(0, 400)} (${CATALOG} lists what this session may use)` } }
  const model = parsed.header.model ?? check?.model, effort = parsed.header.effort ?? check?.effort
  const r = await next({ ...e, prompt: parsed.brief, description: describe(provider, model, effort, title), model: shadowModel(provider) })
  if (r.deny || !r.agentId) return r
  const session = await $.session.id()
  const s = newShadow(r.agentId, provider, title, session, await $.clock.now(), { model, effort, profile: worker.permission_profile, brief: parsed.brief, deliveryOwner: 'mirror' })
  await saveShadow($, s)
  try {
    await request($, 'POST', '/v1/spawn', { client_request_id: `np:${session}:${r.agentId}`, claude_agent_id: r.agentId, worker,
      mirror: { owner: 'claude-mod', claude_session: session, claude_agent_id: r.agentId } })
  } catch (error) {
    s.spawnError = `${error instanceof BridgeError ? error.code : 'BRIDGE_ERROR'}: ${String((error as Error).message).slice(0, 400)}`
    await saveShadow($, s)
  }
  // The desktop app shows the band's content in the pane beside the conversation instead of above the prompt.
  if (o.band !== 'always' && o.band !== 'off' && (await $.session.surface()) === 'desktop') await paneBeside($)
  return r
}

// Starts the bridge again after it exited; leases it held stay valid for their
// TTL, so a restarted bridge for the same session picks them up.
async function ensureBridge($: EngineInterface): Promise<string> {
  if (!place || connected()) return ''
  // In the desktop app the first message starts the session, so this command can arrive while
  // activation still starts the bridge: startBridge waits for that start instead of racing it.
  const launched = await startBridge($)
  if (!launched) return ''
  if ('reason' in launched) return `Bridge restart failed: ${launched.reason}.`
  await ready($, launched.hello)
  void followLineage($)
  return 'Bridge restarted.'
}

async function rebindSession($: EngineInterface): Promise<void> {
  try { await request($, 'POST', '/v1/rebind', { claude_session: await $.session.id() });rebind = false } catch { /* spawns then run under the earlier binding */ }
}

// A shadow for an existing worker (Path B adoption, Path C attach and
// re-attach). Runs only inside a hook frame: a spawn from a timer or a press
// closure skips every hook of this plugin (gate G5).
// A worker whose shadow still runs here keeps it: a second shadow would mirror it twice and hand its delivery back.
async function mirroredHere($: EngineInterface, w: NpWorker): Promise<boolean> {
  if (!w.agentId) return false
  const s = await loadShadow($, w.agentId)
  if (!s || ['COMPLETED', 'FAULTED', 'DETACHED'].includes(s.phase)) return false
  return (await $.agent.list()).some(a => a.id === w.agentId && ['pending', 'running', 'waiting'].includes(a.status))
}

async function attachWorker($: EngineInterface, w: NpWorker, origin: string, backfill: boolean): Promise<string> {
  try { await request($, 'POST', '/v1/mirror/attach', { worker_id: w.workerId, check_only: true }) }
  catch (error) { return `${w.title}: ${error instanceof BridgeError ? error.code : 'BRIDGE_ERROR'}` }
  const brief = adoptionBrief(w.title, w.provider, w.modelRequested, w.effort, w.groupId, w.latest ?? '')
  const r: any = await $.agent.spawn({ subagentType: 'np:' + w.provider, prompt: brief, description: describe(w.provider, w.modelRequested, w.effort, w.title) })
  if (!r.agentId) return `${w.title}: ${r.deny ?? 'not started'}`
  const session = await $.session.id()
  const s = newShadow(r.agentId, w.provider, w.title, session, w.startedAt ?? (await $.clock.now()), { workerId: w.workerId, groupId: w.groupId, taskId: w.taskId,
    generation: w.generation, model: w.modelRequested, effort: w.effort, profile: w.profile, brief, deliveryOwner: 'native', nativeGeneration: w.generation, phase: 'STREAMING', cursor: backfill ? 0 : 0 })
  await saveShadow($, s)
  try {
    await request($, 'POST', '/v1/mirror/attach', { client_request_id: `np-attach:${session}:${w.workerId}:${r.agentId}`, worker_id: w.workerId, claude_agent_id: r.agentId, display_only: true, origin })
  } catch (error) {
    s.spawnError = `${error instanceof BridgeError ? error.code : 'BRIDGE_ERROR'}: ${String((error as Error).message).slice(0, 300)}`;await saveShadow($, s)
  }
  return `${w.title}: attached`
}

async function adopt($: EngineInterface, since: number, o: Options): Promise<void> {
  if (o.adoption === 'off') return
  let data: any
  try { data = await request($, 'GET', '/v1/lineage?wait_ms=0&fresh=1') } catch { return }
  const workers: NpWorker[] = Array.isArray(data.workers) ? data.workers : []
  await setLineage($, workers)
  if (o.adoption === 'ask') return
  for (const w of workers) if (!w.agentId && !w.mirrorOwner && !w.parentWorkerId && (w.startedAt ?? 0) >= since - 2000) await attachWorker($, w, 'adoption', true)
}

async function observeSpawn($: EngineInterface, e: any, next: (e: any) => Promise<any>, o: Options): Promise<any> {
  const since = await $.clock.now()
  const r = await next(e)
  const isBash = e.tool === 'Bash'
  if (isBash && (!/\bnpeer\s+(spawn|resume-worker)\b/.test(String(e.command ?? '')) || /--check\b/.test(String(e.command)))) return r
  await adopt($, since, o)
  return r
}

async function command($: EngineInterface, args: string, o: Options, surface: string): Promise<{ text: string }> {
  const [verb = '', arg = ''] = args.trim().split(/\s+/)
  const workers = await getLineage($)
  const pick = (n: string) => workers[Number(n) - 1]
  if (!verb) return { text: await statusText($) }
  // Claude Code prefixes a command's text with the plugin's name.
  if (verb === 'pane') { await openPane($);return { text: 'Opened the NativePeers pane.' } }
  if (verb === 'doctor') return { text: await doctor($, o, surface, await ensureBridge($)) }
  if (verb === 'open') {
    const w = pick(arg);if (!w) return { text: 'No worker ' + arg + '.' }
    // The main view switches to the agent where the surface draws one and Claude Code still lists it (it drops a
    // finished agent about 30 s after it ends); otherwise the pane shows the conversation.
    if (w.agentId && !(await openView($, w.agentId, true))) return { text: `Viewing ${w.title}.` }
    await openConversation($, w.workerId, true)
    return { text: `Opened ${w.title} in the NativePeers pane.` }
  }
  if (verb === 'stop' && arg === 'all') return { text: `stopping ${await stopAll($)} group(s)` }
  if (verb === 'stop' || verb === 'interrupt' || verb === 'resume-cmd') {
    const w = pick(arg), s = w?.agentId ? await loadShadow($, w.agentId) : undefined
    if (!w) return { text: 'no worker ' + arg }
    const target = s ?? newShadow('', w.provider, w.title, '', 0, { workerId: w.workerId, generation: w.generation })
    if (verb === 'stop') await stopWorker($, target);else if (verb === 'interrupt') await interruptWorker($, target);else await copyResume($, target, surface)
    return { text: `${verb} ${w.title}` }
  }
  if (verb === 'attach' || verb === 'reattach') {
    const restarted = await ensureBridge($)
    if (restarted.startsWith('Bridge restart failed')) return { text: restarted }
    const active = workers.filter(w => ['queued', 'starting', 'running', 'waiting'].includes(w.status) && w.mirrorOwner !== 'other')
    const chosen = verb === 'reattach' ? active : arg === 'all' ? active.filter(w => !w.agentId) : [pick(arg)].filter(Boolean) as NpWorker[]
    const out: string[] = []
    for (const w of chosen) out.push(await mirroredHere($, w) ? `${w.title}: already mirrored` : await attachWorker($, w, verb, true))
    return { text: out.length ? out.join('; ') : 'nothing to attach' }
  }
  return { text: 'unknown subcommand. Try /np, /np pane, /np open N, /np stop N|all, /np interrupt N, /np attach N|all, /np reattach, /np doctor, /np resume-cmd N' }
}

async function doctor($: EngineInterface, o: Options, surface: string, restarted: string): Promise<string> {
  const version = (await $.session.version()).version, bridge = await getBridge($), faults = await getFaults($)
  // The module's own connection is the truth: a state read here may predate a start another hook just finished.
  const live = connected(), generation = connectionGeneration()
  const facts: Parameters<typeof doctorText>[0] = { version, minimum: MIN_ENGINE.join('.'), supported: atLeast(version, MIN_ENGINE) && version.startsWith('2.'), surface,
    npeer: o.npeerPath || '/Users/danny/.local/bin/npeer', connected: live, state: bridge.state, reason: bridge.reason, restarted }
  if (live) {
    try {
      const hello = await request($, 'GET', '/v1/hello')
      if (bridge.state !== 'ready') await restoreReady($, hello, generation)
      facts.hello = { protocol: hello.protocol, providers: hello.providers ?? [], schema: hello.capabilities?.activity_schema }
      if (faults.fallthrough) { await setFaults($, { fallthrough: false });facts.fallthrough = { cleared: true, reason: faults.reason } }
    } catch (error) { facts.helloError = String(error).slice(0, 200) }
  } else if (faults.fallthrough) facts.fallthrough = { cleared: false, reason: faults.reason }
  return doctorText(facts)
}

export const register: Register = (on, options) => {
  const o = options as Options

  on('session.start', async ($, e, next) => {
    const r = await next(e)
    if (await workerMode($)) { inert = true;return r }
    // The desktop composer accepts the commands a session has at start; activation there waits for its surface.
    await $.command.register(COMMAND)
    if (e.isInteractive || (await $.env.get('NP_MOD_HEADLESS')) === '1') await activate($, e.cwd, o)
    return r
  })
  // A desktop-hosted session can start with no surface and attach one later.
  on('session.attach', async ($, e, next) => {
    const r = await next(e)
    if (!inert && (e.surface === 'desktop' || e.surface === 'vscode')) await activate($, await $.session.cwd(), o)
    return r
  })
  on('session.end', async ($, e, next) => {
    const r = await next(e)
    if (e.reason === 'clear') rebind = true;else await shutdown($)
    return r
  })

  on('agent.offer', async ($, e, next) => {
    if (!String(e.agent).startsWith('np:')) return next(e)
    return { isOffered: (await getBridge($)).state === 'ready' && !(await getFaults($)).fallthrough }
  }).catch(($, e, next) => String(e.agent).startsWith('np:') ? { isOffered: false } : next(e))
  // Path A is the engine's own Agent call. A spawn this mod raised itself
  // (adoption, attach) passes through: rewriting it would detach it from the
  // frame that raised it.
  on('agent.spawn', async ($, e, next) => String(e.subagentType).startsWith('np:') && (next as any).origin?.plugin !== 'np' ? pathA($, e, next, o) : next(e))
    .catch(($, e, next) => (next as any).called ? next(e) : { deny: 'NativePeers spawn failed; run /np doctor' })

  on('prompt.compose', async ($, e, next) => {
    const r = await next(e)
    if ((await getBridge($)).state !== 'ready') return r
    return { ...r, sections: [...r.sections, { id: 'np-lineage', text: SECTION, scope: 'session' }] }
  })

  on('turn.step', async function* npStep($, e: TurnStepInput, next): AsyncGenerator<any, TurnStepResult> {
    if (e.agentId === undefined || inert) return yield* next(e)
    let s = await loadShadow($, e.agentId)
    // A step on a np-mirror model is a shadow's, recorded or not: it is never
    // passed through. The record may land a moment after the spawn resolves.
    for (let n = 0;!s && e.model.startsWith(MODEL_PREFIX) && n < 20;n++) { await sleep($, 250);s = await loadShadow($, e.agentId) }
    if (!s) {
      if (!e.model.startsWith(MODEL_PREFIX)) return yield* next(e)
      const text = 'NativePeers has no record of this shadow; its worker is unaffected. /np reattach shows it again.'
      yield { kind: 'text', index: 0, text };yield { kind: 'stop', stopReason: 'end_turn', usage: null }
      return { turnId: e.turnId, index: e.index, answer: text, toolUses: [], stopReason: 'end_turn', usage: null }
    }
    const steer = Boolean((await getBridge($)).steer?.[s.provider as NpProvider])
    const r = yield* stepShadow($, e, s, { showReasoning: o.showReasoning !== 'off', followupMode: o.followupMode === 'steer-if-supported' ? 'steer-if-supported' : 'queue', steer })
    // Own time for the performance budget (spec 11): waits on $ calls do not count.
    $.ui.log(`np: step own-time ${Math.round(next.budget.ms - next.budget.remainingMs)}ms ${s.provider} ${e.agentId} step ${e.index}`, { to: 'debug' })
    return r
  }).catch(async function* npStepFault($, e: TurnStepInput, next): AsyncGenerator<any, TurnStepResult> {
    // Fail closed for shadows: answer with the fault, never call next. Other
    // loops replay what was beneath them.
    if (e.agentId === undefined || !e.model.startsWith(MODEL_PREFIX)) return yield* next(e)
    const kind = (next as any).error?.kind ?? 'unknown'
    const text = `\n[NativePeers mirror fault: ${kind}. The worker is unaffected; /np reattach or /np doctor.]`
    yield { kind: 'text', index: 0, text };yield { kind: 'stop', stopReason: 'end_turn', usage: null }
    void faultShadow($, e.agentId, kind)
    $.ui.toast(`Mirror fault (${kind}). The worker keeps running.`)
    return { turnId: e.turnId, index: e.index, answer: text, toolUses: [], stopReason: 'end_turn', usage: null }
  })

  on('tool.call', { tool: MIRROR_TOOL }, async ($, e, next) => mirrorCall($, e.agentId, e, Number(o.mirrorOutputBytes ?? 8192), next.signal))
    .catch(() => ({ deny: 'NativePeers mirror unavailable' }))
  on('tool.call', { tool: CATALOG }, async ($, e) => ({ result: JSON.stringify(await request($, 'GET', '/v1/catalog' + ((e as any).provider ? '?provider=' + (e as any).provider : ''))) }))
    .catch(() => ({ deny: 'NativePeers catalog unavailable — run /np doctor' }))
  on('tool.call', { tool: 'Bash' }, async ($, e, next) => observeSpawn($, e, next, o)).catch(($, e, next) => next(e))
  // A pattern, not names: the nativepeers MCP server may be absent from the session the types were written in.
  on('tool.call', { tool: /^mcp__nativepeers__worker_(spawn|resume)$/ }, async ($, e, next) => observeSpawn($, e, next, o)).catch(($, e, next) => next(e))

  on('tool.check', async ($, e, next) => {
    const s = e.agentId ? await loadShadow($, e.agentId) : undefined
    if (s) {
      if (e.tool === MIRROR_TOOL && s.pendingRefs[String((e.input as any)?.ref)]) return { decision: 'allow' as const, reason: 'NativePeers mirror replay' }
      // A run the engine ends with SubagentHandback: only auto mode's classifier may allow that call (it vets what reaches
      // the parent), so a shadow holding its report leaves the verdict to it.
      if (e.tool === HANDBACK_TOOL && s.report !== undefined) return next(e)
      return { decision: 'deny' as const, reason: 'NativePeers shadows have no tools but the mirror' }
    }
    if (e.tool === MIRROR_TOOL) return { decision: 'deny' as const, reason: 'Internal NativePeers display tool' }
    return next(e)
  }).catch(($, e, next) => e.tool === MIRROR_TOOL ? { decision: 'deny' as const } : next(e))

  on('session.append', async ($, e, next) => {
    const r = await next(e)
    if (e.agentId) await captureRow($, e.agentId, e as any, e.uuid)
    return r
  }).catch(($, e, next) => next(e))

  on('session.compact', async ($, e, next) => (await loadShadow($, (e as any).agentId)) ? { skip: 'NativePeers shadow transcripts are not compacted' } : next(e))
    .catch(($, e, next) => next(e))

  on('turn.complete', async ($, e, next) => {
    const r = await next(e)
    if (e.agentId && !inert) await completeShadow($, e.agentId, (e as any).reason, e.isAborted, e.answer, e.usage)
    return r
  })

  on('command.run', { command: 'np' }, async ($, e) => command($, e.args, o, String((e as any).presentation?.surface ?? 'terminal')))
  on('ui.press', async ($, e, next) => {
    if (e.plugin !== 'np' || !e.element.startsWith('attach:')) return next(e)
    const w = (await getLineage($)).find(x => x.workerId === e.element.slice(7))
    if (w) $.ui.toast(await attachWorker($, w, 'ui.press', true))
    return { element: e.element }
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => o.band === 'off' ? next(e) : renderBand($, e, next, o.band === 'always'))
  on('ui.render', { component: 'Pane', requestId: PANE_ID }, async ($, e, next) => renderPane($, e, next, o.band !== 'always' && o.band !== 'off'))
  on('ui.render', { component: 'ToolUse', props: { tool: MIRROR_TOOL } }, async ($, e, next) => renderToolUse($, e, next))
  on('ui.render', { component: 'UserMessage', props: { origin: { kind: 'coordinator' } } }, async ($, e, next) => renderUserMessage($, e, next))
  on('ui.render', { component: 'Spinner' }, async ($, e, next) => renderSpinner($, e, next))
}
