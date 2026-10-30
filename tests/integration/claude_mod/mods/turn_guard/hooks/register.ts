import type { EngineInterface, Register, TurnStepInput, TurnStepResult } from 'claude-code'

// Live-test only. The first NP_GUARD_REAL_TURNS main-loop turns reach the real
// model; every later main turn (a background-agent notification, typically) is
// answered here and logged to NP_GUARD_LOG, so observing a notification spends
// no parent-model turn. Subagent loops are never touched.
let turns = 0
const lines: string[] = []
const instance = Math.random().toString(36).slice(2, 8)

async function log($: EngineInterface, kind: string, data: unknown): Promise<void> {
  lines.push(JSON.stringify({ at: await $.clock.now(), kind, data }))
  const path = await $.env.get('NP_GUARD_LOG')
  if (path) await $.fs.write(path + '.' + instance, lines.join('\n') + '\n')
}

async function lastPrompt($: EngineInterface): Promise<string> {
  const messages: any = await $.session.messages()
  const users = Array.isArray(messages) ? messages.filter((m: any) => m.role === 'user') : []
  return String(users[users.length - 1]?.text ?? '').slice(0, 2000)
}

export const register: Register = on => {
  on('turn.step', async function* guardStep($, e: TurnStepInput, next): AsyncGenerator<any, TurnStepResult> {
    if (e.agentId !== undefined) return yield* next(e)
    if (e.index === 0) turns += 1
    const real = Number((await $.env.get('NP_GUARD_REAL_TURNS')) ?? '1')
    if (turns <= real) {
      if (e.index === 0) await log($, 'real-turn', { turn: turns, model: e.model })
      return yield* next(e)
    }
    const prompt = await lastPrompt($)
    await log($, 'guarded-turn', { turn: turns, index: e.index, prompt })
    const text = '(live harness: this main turn was answered locally; no parent model request)'
    yield { kind: 'text', index: 0, text }
    yield { kind: 'stop', stopReason: 'end_turn', usage: null }
    return { turnId: e.turnId, index: e.index, answer: text, toolUses: [], stopReason: 'end_turn', usage: null }
  }).catch(async function* guardFault($, e: TurnStepInput, next): AsyncGenerator<any, TurnStepResult> {
    if (e.agentId !== undefined || turns <= Number((await $.env.get('NP_GUARD_REAL_TURNS')) ?? '1')) return yield* next(e)
    const text = '(live harness guard fault)'
    yield { kind: 'text', index: 0, text };yield { kind: 'stop', stopReason: 'end_turn', usage: null }
    return { turnId: e.turnId, index: e.index, answer: text, toolUses: [], stopReason: 'end_turn', usage: null }
  })
}
