import type { EngineInterface, Register, TurnStepInput, TurnStepResult } from 'claude-code'

// Run only by `npeer runtime-check --provider claude`, headless, behind a
// counting tripwire. It answers the main loop itself, spawns one probe shadow
// on an unserved model from a tool.call frame, answers its steps (one through
// a replay-only tool), lets the last step throw into a .catch that returns a
// complete result, and reports what it saw in its final answer.
const shadows = new Set<string>()
const seen: string[] = []
let done = ''

function finish(e: TurnStepInput, answer: string, stop: 'end_turn' | 'tool_use', uses: { name: string; input: unknown }[] = []): TurnStepResult {
  return { turnId: e.turnId, index: e.index, answer, toolUses: uses, stopReason: stop, usage: null }
}

async function spawnProbe($: EngineInterface): Promise<string> {
  const r: any = await $.agent.spawn({ subagentType: 'npcheck:probe', prompt: 'probe brief', description: 'np compatibility probe' })
  if (!r.agentId) return 'spawn refused: ' + String(r.deny ?? 'unknown')
  shadows.add(r.agentId)
  const started = await $.clock.now()
  while (!done && (await $.clock.now()) - started < 20000) await $.process.run(['/bin/sleep', '0.2'], { timeoutMs: 5000 })
  return done || 'probe did not finish'
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const r = await next(e)
    await $.agent.register({ name: 'probe', description: 'compatibility probe', prompt: 'Reply exactly: NPCHECK-FALLTHROUGH', tools: ['mcp__npcheck__replay'],
      model: 'np-mirror-check', omitClaudeMd: true, background: true, permissionMode: 'dontAsk' })
    await $.tool.register({ name: 'start', description: 'probe', isDeferred: false, inputSchema: { type: 'object', properties: {} } })
    await $.tool.register({ name: 'replay', description: 'probe', isDeferred: false, inputSchema: { type: 'object', properties: {} } })
    return r
  })
  on('tool.call', { tool: 'mcp__npcheck__start' }, async ($) => ({ result: await spawnProbe($) }))
  on('tool.call', { tool: 'mcp__npcheck__replay' }, async () => { seen.push('replay');return { result: 'replayed' } })
  on('turn.complete', async ($, e, next) => {
    if (e.agentId && shadows.has(e.agentId)) done = `shadow=${e.answer.trim()} reason=${(e as any).reason} steps=${seen.join(',')}`
    return next(e)
  })
  on('turn.step', async function* checkStep($, e, next): AsyncGenerator<any, TurnStepResult> {
    if (e.agentId === undefined) {
      if (e.index === 0) {
        yield { kind: 'tool', index: 0, id: 'toolu_01npcheck0000000000start', name: 'mcp__npcheck__start' }
        yield { kind: 'input', index: 0, json: '{}' }
        yield { kind: 'stop', stopReason: 'tool_use', usage: null }
        return finish(e, '', 'tool_use', [{ name: 'mcp__npcheck__start', input: {} }])
      }
      const messages: any = await $.session.messages()
      const last = Array.isArray(messages) ? messages[messages.length - 1] : undefined
      const text = 'npcheck: ' + String(last?.toolResults?.[0]?.text ?? last?.text ?? 'no result')
      yield { kind: 'text', index: 0, text };yield { kind: 'stop', stopReason: 'end_turn', usage: null }
      return finish(e, text, 'end_turn')
    }
    if (!shadows.has(e.agentId)) return yield* next(e)
    seen.push('step' + e.index + ':' + e.model)
    if (e.index === 0) {
      yield { kind: 'tool', index: 0, id: 'toolu_01npcheck000000000replay', name: 'mcp__npcheck__replay' }
      yield { kind: 'input', index: 0, json: '{}' }
      yield { kind: 'stop', stopReason: 'tool_use', usage: null }
      return finish(e, '', 'tool_use', [{ name: 'mcp__npcheck__replay', input: {} }])
    }
    throw new Error('deliberate probe fault')
  }).catch(async function* checkFault($, e, next): AsyncGenerator<any, TurnStepResult> {
    if (e.agentId === undefined || !shadows.has(e.agentId)) return yield* next(e)
    const text = 'contained:' + String((next as any).error?.kind ?? '?')
    yield { kind: 'text', index: 0, text };yield { kind: 'stop', stopReason: 'end_turn', usage: null }
    return finish(e, text, 'end_turn')
  })
}
