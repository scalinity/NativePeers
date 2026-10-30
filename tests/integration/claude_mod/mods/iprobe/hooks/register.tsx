import { atom, read, update } from 'claude-code'
import type { Register, EngineInterface, TurnStepInput, TurnStepResult } from 'claude-code'

// Harness-only probes for the interactive gates. Env: NP_PROBE_LOG (JSON lines),
// NP_PROBE_HOLD_MS (mirror hold), NP_PROBE_PERMISSION (AgentSpec.permissionMode),
// NP_PROBE_TIMER_MS (spawn from a timer), NP_PROBE_START_SPAWN=1 (spawn from
// session.start), NP_PROBE_STEPS (tool steps per run).
const shadowsRef = atom({ plugin: 'npi', key: 'shadows' } as const, [])
const lastRef = atom({ plugin: 'npi', key: 'last' } as const, '')
const known = new Set<string>()
const runs: Record<string, number> = {}
const lines: string[] = []
const instance = Math.random().toString(36).slice(2, 8)

async function note($: EngineInterface, kind: string, data: unknown): Promise<void> {
  lines.push(JSON.stringify({ at: await $.clock.now(), kind, data }))
  const path = await $.env.get('NP_PROBE_LOG')
  if (path) await $.fs.write(path + '.' + instance, lines.join('\n') + '\n')
}

async function remember($: EngineInterface, agentId: string, origin: string): Promise<void> {
  known.add(agentId)
  await update($, shadowsRef, list => [...(list ?? []), agentId])
  await note($, 'shadow', { agentId, origin })
}

async function spawnShadow($: EngineInterface, origin: string): Promise<string> {
  const r: any = await $.agent.spawn({ subagentType: 'npi:mirror', prompt: `BRIEF from ${origin}`, description: `probe · ${origin}` })
  if (r.agentId) await remember($, r.agentId, origin)
  await note($, 'spawn', { origin, result: r })
  return JSON.stringify(r)
}

async function openShadow($: EngineInterface, agentId: string, origin: string): Promise<void> {
  const r = await $.ui.view({ agentId })
  await note($, 'ui.view', { origin, agentId, result: r })
  await update($, lastRef, () => `view ${origin}: ${JSON.stringify(r)}`)
}

async function mainView($: EngineInterface): Promise<void> {
  const r = await $.ui.view({})
  await note($, 'ui.view', { origin: 'ui.press', agentId: null, result: r })
}

async function sleepFor($: EngineInterface, ms: number): Promise<void> {
  let left = ms
  while (left > 0) { const c = Math.min(left, 240000); await $.process.run(['sleep', String(c / 1000)], { timeoutMs: c + 30000 }); left -= c }
}

function finish(e: TurnStepInput, answer: string, stopReason: 'end_turn' | 'tool_use', toolUses: { name: string; input: unknown }[] = []): TurnStepResult {
  return { turnId: e.turnId, index: e.index, answer, toolUses, stopReason, usage: null }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const r = await next(e)
    const permissionMode = await $.env.get('NP_PROBE_PERMISSION')
    await $.agent.register({ name: 'mirror', description: 'Probe shadow (harness only)', prompt: 'Reply exactly: MIRROR-FALLTHROUGH',
      tools: ['mcp__npi__mirror'], model: 'np-mirror-probe', omitClaudeMd: true, background: true,
      ...(permissionMode ? { permissionMode } : {}) })
    await $.tool.register({ name: 'mirror', description: 'harness only', isDeferred: false,
      inputSchema: { type: 'object', properties: { title: { type: 'string' } } } })
    await $.tool.register({ name: 'spawn_shadow', description: 'harness only', isDeferred: false, inputSchema: { type: 'object', properties: {} } })
    await $.command.register({ name: 'npi-open', description: 'probe: view the newest shadow', immediate: true })
    await $.command.register({ name: 'npi-list', description: 'probe: print $.agent.list()', immediate: true })
    await $.command.register({ name: 'npi-spawn', description: 'probe: spawn a shadow from command.run', immediate: true })
    await note($, 'session.start', { shadowsInState: (await read($, shadowsRef)).length, id: await $.session.id(), isInteractive: (e as any).isInteractive, surface: (e as any).surface ?? null, version: await $.session.version() })
    const timer = await $.env.get('NP_PROBE_TIMER_MS')
    if (timer) $.clock.after(Number(timer), () => spawnShadow($, 'timer'))
    if ((await $.env.get('NP_PROBE_START_SPAWN')) === '1') await spawnShadow($, 'session.start')
    return r
  })

  on('session.end', async ($, e, next) => { await note($, 'session.end', { reason: (e as any).reason }); return next(e) })
  on('session.attach', async ($, e, next) => { await note($, 'session.attach', e); return next(e) })

  on('agent.spawn', async ($, e, next) => {
    const r = await next(e)
    if (e.subagentType === 'npi:mirror' && r.agentId && !e.resume) await remember($, r.agentId, 'agent.spawn:' + ((next as any).origin?.plugin ?? '?'))
    await note($, 'agent.spawn', { subagentType: e.subagentType, resume: e.resume ?? null, permissionMode: e.permissionMode, origin: (next as any).origin ?? null, result: r })
    return r
  })

  on('command.run', { command: 'npi-list' }, async ($, e) => {
    const agents = await $.agent.list()
    await note($, 'agent.list', { session: await $.session.id(), agents, state: await read($, shadowsRef) })
    return { text: 'agents ' + JSON.stringify(agents.map(a => [a.id, a.status, a.type])) + ' state ' + JSON.stringify(await read($, shadowsRef)) }
  })
  on('command.run', { command: 'npi-spawn' }, async ($, e) => ({ text: 'spawned ' + (await spawnShadow($, 'command.run')) }))
  on('command.run', { command: 'npi-open' }, async ($, e) => {
    const list = await read($, shadowsRef)
    if (list.length) await openShadow($, list[list.length - 1], 'command.run')
    return { text: 'opened ' + (await read($, lastRef)) }
  })
  on('ui.press', { element: 'spawn2' }, async ($, e) => { await spawnShadow($, 'ui.press-hook'); return {} as any })
  on('tool.call', { tool: 'mcp__npi__spawn_shadow' }, async ($, e) => ({ result: 'spawned ' + (await spawnShadow($, 'tool.call')) }))

  on('tool.call', { tool: 'mcp__npi__mirror' }, async ($, e) => {
    const started = await $.clock.now()
    await note($, 'mirror.start', { agentId: (e as any).agentId ?? null })
    await sleepFor($, Number((await $.env.get('NP_PROBE_HOLD_MS')) ?? '30000'))
    await note($, 'mirror.end', { agentId: (e as any).agentId ?? null, ms: (await $.clock.now()) - started, aborted: (e as any).signal?.aborted ?? null })
    return { result: 'exit 0 · held' }
  })

  on('tool.check', async ($, e, next) => {
    const r = await next(e)
    if (String((e as any).tool).startsWith('mcp__npi__')) await note($, 'tool.check', { tool: (e as any).tool, agentId: (e as any).agentId ?? null, mode: (e as any).mode ?? null, result: r })
    return r
  })

  on('session.append', async ($, e, next) => {
    const r = await next(e)
    if (e.agentId || e.door === 'delivery' || e.door === 'queue' || (e.origin as any).kind === 'task-notification') {
      const m: any = e.message
      const text = Array.isArray(m.content) ? m.content.filter((b: any) => b.type === 'text').map((b: any) => b.text).join('|') : String(m.content ?? '')
      await note($, 'session.append', { agentId: e.agentId ?? null, door: e.door, origin: e.origin, uuid: r.uuid, type: m.type, blocks: Array.isArray(m.content) ? m.content.map((b: any) => b.type) : [], text: text.slice(0, 1200) })
    }
    return r
  })
  on('session.receive', async ($, e, next) => { await note($, 'session.receive', { agentId: e.agentId ?? null, origin: e.origin, event: e.event ?? null, text: e.text.slice(0, 300) }); return next(e) })
  on('session.send', async ($, e, next) => { const r = await next(e); await note($, 'session.send', { input: e, result: r }); return r })
  on('prompt.submit', async ($, e, next) => { await note($, 'prompt.submit', { origin: (e as any).origin, agentId: (e as any).agentId ?? null, isMeta: (e as any).isMeta, text: String((e as any).text).slice(0, 200) }); return next(e) })
  on('session.compact', async ($, e, next) => { await note($, 'session.compact', { agentId: (e as any).agentId ?? null, trigger: (e as any).trigger }); return next(e) })

  on('turn.complete', async ($, e, next) => {
    if (e.agentId && known.has(e.agentId)) {
      const info = (await $.agent.list()).find(a => a.id === e.agentId)
      await note($, 'turn.complete', { agentId: e.agentId, reason: (e as any).reason, isAborted: e.isAborted, status: info?.status ?? null, usage: e.usage ?? null, answer: e.answer.slice(0, 120) })
    }
    return next(e)
  })

  on('ui.render', { component: 'Spinner' }, async ($, e, next) => {
    if (known.has(e.requestId)) {
      await note($, 'spinner', { requestId: e.requestId, props: e.props, surface: e.surface })
      return next({ ...e, props: { ...e.props, message: 'cx: probe activity line' } })
    }
    return next(e)
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const list = await read($, shadowsRef)
    const last = await read($, lastRef)
    const { Box, Button, Text } = $.ui.resolve(e)
    const view = e.props.view?.agentId ?? 'main'
    return (
      <Box flexDirection="column">
        <Text dimColor>npi · view {view} · {list.length} shadows · {last}</Text>
        <Box>
          {list.slice(-3).map((id, n) => <Button key={'open-' + id} label={'Open ' + (n + 1)} onPress={() => openShadow($, id, 'ui.press')} />)}
          <Button key="spawn" label="Spawn" hotkey="s" onPress={() => spawnShadow($, 'ui.press')} />
          <Button key="main" label="Main" hotkey="m" onPress={() => mainView($)} />
          <Button key="spawn2" label="Spawn (hook)" hotkey="h" onPress={() => undefined} />
        </Box>
      </Box>
    )
  })

  on('turn.step', async function* probeStep($, e, next): AsyncGenerator<any, TurnStepResult> {
    if (e.agentId && !known.has(e.agentId) && (await read($, shadowsRef)).includes(e.agentId)) {
      known.add(e.agentId)
      await note($, 'reattached-from-state', { agentId: e.agentId, index: e.index })
    }
    if (!e.agentId || !known.has(e.agentId)) return yield* next(e)
    const run = e.index === 0 ? (runs[e.agentId] = (runs[e.agentId] ?? 0) + 1) : runs[e.agentId]
    const messages = await $.session.messages({ agentId: e.agentId })
    await note($, 'turn.step', { agentId: e.agentId, index: e.index, run, model: e.model, messageCount: e.messageCount,
      tail: messages.slice(-2).map((m: any) => ({ role: m.role, text: String(m.text ?? '').slice(0, 160) })) })
    const total = Number((await $.env.get('NP_PROBE_STEPS')) ?? '1')
    if (e.index < total) {
      const id = 'toolu_01npi' + Math.random().toString(36).slice(2, 12) + Math.random().toString(36).slice(2, 10)
      yield { kind: 'text', index: 0, text: `probe run ${run} step ${e.index}: working` }
      yield { kind: 'tool', index: 1, id, name: 'mcp__npi__mirror' }
      yield { kind: 'input', index: 1, json: JSON.stringify({ title: 'pytest -q' }) }
      yield { kind: 'stop', stopReason: 'tool_use', usage: null }
      return finish(e, `probe run ${run} step ${e.index}: working`, 'tool_use', [{ name: 'mcp__npi__mirror', input: { title: 'pytest -q' } }])
    }
    const text = `probe run ${run} finished`
    yield { kind: 'text', index: 0, text }
    yield { kind: 'stop', stopReason: 'end_turn', usage: null }
    return finish(e, text, 'end_turn')
  }).catch(async function* probeFault($, e, next): AsyncGenerator<any, TurnStepResult> {
    const text = `[probe fault: ${next.error?.kind ?? 'unknown'}]`
    yield { kind: 'text', index: 0, text }
    yield { kind: 'stop', stopReason: 'end_turn', usage: null }
    return finish(e, text, 'end_turn')
  })
}
