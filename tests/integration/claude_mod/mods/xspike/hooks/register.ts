import type { Register, EngineInterface, TurnStepInput, TurnStepResult } from 'claude-code'

// Harness-only. NP_SPIKE_MODE selects the shadow behaviour:
//   normal | throw | overrun | stall | hold130 | catch_noreturn
// NP_SPIKE_HOLD_MS holds each mirror tool call (X4). Everything observed is
// appended to NP_SPIKE_LOG as JSON lines.
const shadows = new Set<string>()
const done = new Set<string>()
const runs: Record<string, number> = {}
const lines: string[] = []
let lastShadow = ''

async function note($: EngineInterface, kind: string, data: unknown): Promise<void> {
  lines.push(JSON.stringify({ at: await $.clock.now(), kind, data }))
  const path = await $.env.get('NP_SPIKE_LOG')
  if (path) await $.fs.write(path, lines.join('\n') + '\n')
}

async function mode($: EngineInterface): Promise<string> {
  return (await $.env.get('NP_SPIKE_MODE')) ?? 'normal'
}

async function sleepFor($: EngineInterface, ms: number): Promise<void> {
  // $.process.run caps at ten minutes, so long waits loop in bounded chunks.
  let left = ms
  while (left > 0) {
    const chunk = Math.min(left, 240000)
    await $.process.run(['sleep', String(chunk / 1000)], { timeoutMs: chunk + 30000 })
    left -= chunk
  }
}

function toolId(): string {
  return 'toolu_01spk' + Math.random().toString(36).slice(2, 12) + Math.random().toString(36).slice(2, 10)
}

function finish(e: TurnStepInput, answer: string, stopReason: 'end_turn' | 'tool_use', toolUses: { name: string; input: unknown }[] = [], usage: any = null): TurnStepResult {
  return { turnId: e.turnId, index: e.index, answer, toolUses, stopReason, usage }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const r = await next(e)
    const maxTurns = await $.env.get('NP_SPIKE_MAXTURNS')
    const spec = await $.agent.register({ name: 'mirror', description: 'Mirror of an external worker (harness only)',
      prompt: 'You are a display placeholder. Reply exactly: MIRROR-FALLTHROUGH', tools: ['mcp__npx__mirror_exec'],
      model: (await $.env.get('NP_SPIKE_AGENT_MODEL')) ?? 'haiku', omitClaudeMd: true, background: true,
      ...(maxTurns ? { maxTurns: Number(maxTurns) } : {}) })
    await note($, 'agent.register', spec)
    for (const name of ['mirror_exec', 'spawn_shadow', 'wait_shadow', 'send_shadow'])
      await $.tool.register({ name, description: 'harness only', isDeferred: false,
        inputSchema: { type: 'object', properties: { command: { type: 'string' } } } })
    await note($, 'session.start', { version: await $.session.version(), id: await $.session.id(), isInteractive: (e as any).isInteractive })
    return r
  })

  on('agent.spawn', async ($, e, next) => {
    const r = await next(e)
    if (e.subagentType === 'npx:mirror' && r.agentId) { shadows.add(r.agentId); lastShadow = r.agentId }
    await note($, 'agent.spawn', { subagentType: e.subagentType, resume: e.resume ?? null, background: e.background, origin: (next as any).origin ?? null, result: r })
    return r
  })

  on('tool.call', { tool: 'mcp__npx__spawn_shadow' }, async ($, e) => {
    const r: any = await $.agent.spawn({ subagentType: 'npx:mirror', prompt: 'BRIEF: echo hello', description: 'codex gpt-6.1-sol review' })
    if (r.agentId) { shadows.add(r.agentId); lastShadow = r.agentId }
    await note($, 'spawn_shadow', { result: r, list: await $.agent.list() })
    return { result: 'spawned ' + JSON.stringify(r) }
  })

  on('tool.call', { tool: 'mcp__npx__wait_shadow' }, async ($, e) => {
    const started = await $.clock.now()
    const cap = Number((await $.env.get('NP_SPIKE_WAIT_MS')) ?? '240000')
    while (!done.has(lastShadow) && (await $.clock.now()) - started < cap) await sleepFor($, 500)
    done.delete(lastShadow)
    return { result: 'waited ' + lastShadow + ' list=' + JSON.stringify(await $.agent.list()) }
  })

  on('tool.call', { tool: 'mcp__npx__send_shadow' }, async ($, e) => {
    const r = await $.session.send({ to: { agentId: lastShadow }, text: 'FOLLOWUP: also check the tests' })
    await note($, 'session.send', { to: lastShadow, result: r })
    return { result: 'sent ' + JSON.stringify(r) }
  })

  on('tool.call', { tool: 'mcp__npx__mirror_exec' }, async ($, e) => {
    const started = await $.clock.now()
    const hold = Number((await $.env.get('NP_SPIKE_HOLD_MS')) ?? '500')
    await sleepFor($, hold)
    await note($, 'mirror_exec.end', { agentId: (e as any).agentId, ms: (await $.clock.now()) - started })
    return { result: 'exit_code: 0\nstdout: hello from the external worker' }
  })

  on('session.append', async ($, e, next) => {
    const r = await next(e)
    const m: any = e.message
    const blocks = Array.isArray(m.content) ? m.content.map((b: any) => b.type) : typeof m.content
    const text = Array.isArray(m.content) ? m.content.filter((b: any) => b.type === 'text').map((b: any) => b.text).join('|').slice(0, 200) : String(m.content ?? '').slice(0, 200)
    await note($, 'session.append', { agentId: e.agentId ?? null, door: e.door, origin: e.origin, uuid: r.uuid, type: m.type, blocks, text })
    return r
  })

  on('prompt.submit', async ($, e, next) => {
    await note($, 'prompt.submit', { origin: (e as any).origin ?? null, isMeta: (e as any).isMeta, turnId: (e as any).turnId ?? null, text: String((e as any).text ?? '').slice(0, 300) })
    return next(e)
  })

  on('turn.complete', async ($, e, next) => {
    if (e.agentId && shadows.has(e.agentId)) done.add(e.agentId)
    await note($, 'turn.complete', { agentId: e.agentId ?? null, reason: (e as any).reason, isAborted: e.isAborted, usage: e.usage ?? null, answer: e.answer.slice(0, 200) })
    return next(e)
  })

  on('turn.step', async function* shadowStep($, e, next): AsyncGenerator<any, TurnStepResult> {
    if (e.agentId && !shadows.has(e.agentId)) {
      const agents = await $.agent.list()
      await note($, 'turn.step.other', { agentId: e.agentId, index: e.index, model: e.model, messageCount: e.messageCount, listed: agents.some(a => a.id === e.agentId), shadows: [...shadows] })
      if ((await $.env.get('NP_SPIKE_ANSWER_FORKS')) === '1') {
        const text = 'np: summary answered by the mod'
        yield { kind: 'text', index: 0, text }
        yield { kind: 'stop', stopReason: 'end_turn', usage: null }
        return finish(e, text, 'end_turn')
      }
    }
    if (!e.agentId || !shadows.has(e.agentId)) return yield* next(e)
    const m = await mode($)
    const run = e.index === 0 ? (runs[e.agentId] = (runs[e.agentId] ?? 0) + 1) : runs[e.agentId]
    await note($, 'turn.step', { agentId: e.agentId, index: e.index, run, model: e.model, budget: (next as any).budget ?? null })
    if (m === 'throw' || m === 'catch_noreturn') {
      yield { kind: 'text', index: 0, text: 'about to fault' }
      throw new Error('spike fault')
    }
    if (m === 'overrun') { const end = Date.now() + 11000; while (Date.now() < end) {} }
    if (m === 'stall' && typeof setTimeout === 'function') await new Promise(r => setTimeout(r, 12000))
    if (m === 'hold130') await $.process.run(['sleep', '130'], { timeoutMs: 180000 })
    if (m.startsWith('silent:') && e.index === 0) await sleepFor($, Number(m.slice(7)))
    const total = m.startsWith('steps:') ? Number(m.slice(6)) : 1
    if (e.index < total) {
      yield { kind: 'thinking', index: 0, text: 'spike thinking (should not be recorded)' }
      yield { kind: 'text', index: 1, text: `Mirroring external worker run ${run}: running echo hello` }
      yield { kind: 'tool', index: 2, id: toolId(), name: 'mcp__npx__mirror_exec' }
      yield { kind: 'input', index: 2, json: JSON.stringify({ command: 'echo hello' }) }
      yield { kind: 'stop', stopReason: 'tool_use', usage: null }
      return finish(e, `Mirroring external worker run ${run}: running echo hello`, 'tool_use', [{ name: 'mcp__npx__mirror_exec', input: { command: 'echo hello' } }])
    }
    const usage = { input_tokens: 0, output_tokens: 42, cache_read_input_tokens: 0, cache_creation_input_tokens: 0, model: 'spike-observed-model' }
    const text = `External worker finished run ${run}: hello`
    yield { kind: 'text', index: 0, text }
    yield { kind: 'stop', stopReason: 'end_turn', usage }
    await note($, 'signal', { aborted: next.signal.aborted })
    return finish(e, text, 'end_turn', [], usage)
  }).catch(async function* shadowFault($, e, next): AsyncGenerator<any, TurnStepResult | void> {
    const text = `\n[mirror fault: ${next.error?.kind ?? 'unknown'}]`
    yield { kind: 'text', index: 0, text }
    yield { kind: 'stop', stopReason: 'end_turn', usage: null }
    await note($, 'catch', { kind: next.error?.kind ?? null, agentId: e.agentId })
    if ((await mode($)) === 'catch_noreturn') return
    return finish(e, text, 'end_turn')
  })
}
