import type { EngineInterface, Register, TurnStepInput, TurnStepResult } from 'claude-code'

// Live-test only. The first NP_GUARD_REAL_TURNS main-loop turns reach the real
// model. A later main turn whose prompt holds a key of NP_GUARD_SCRIPT's "on"
// map runs that scripted plan ({ text?, tools?: [{ name, input }] } steps);
// every other later turn (a background-agent notification, typically) is
// answered locally. All of it is logged to NP_GUARD_LOG, so observing a
// notification spends no parent-model turn. Subagent loops are never touched.
type Step = { text?: string; tools?: { name: string; input: unknown }[] }
let turns = 0
let plan: Step[] | undefined
let script: { on?: Record<string, Step[]> } | undefined
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
  return String(users[users.length - 1]?.text ?? '').slice(0, 4000)
}

async function loadScript($: EngineInterface): Promise<{ on?: Record<string, Step[]> }> {
  if (script) return script
  const path = await $.env.get('NP_GUARD_SCRIPT')
  script = path ? JSON.parse(await $.fs.read(path)) : {}
  return script!
}

async function resolve($: EngineInterface, value: unknown): Promise<unknown> {
  if (typeof value === 'string') {
    const match = /\$\{agent(\d?):([^}]+)\}/.exec(value)
    if (!match) return value
    const agents = (await $.agent.list()).filter(a => a.type === match[2])
    const pick = match[1] ? agents[Number(match[1]) - 1] : agents[agents.length - 1]
    return value.replace(match[0], pick ? pick.id : 'missing-agent')
  }
  if (Array.isArray(value)) { const out = []; for (const v of value) out.push(await resolve($, v)); return out }
  if (value && typeof value === 'object') {
    const out: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(value)) out[k] = await resolve($, v)
    return out
  }
  return value
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
    if (e.index === 0) {
      const prompt = await lastPrompt($)
      const found = Object.entries((await loadScript($)).on ?? {}).find(([key]) => prompt.includes(key))
      plan = found ? [...found[1]] : undefined
      await log($, found ? 'scripted-turn' : 'guarded-turn', { turn: turns, key: found?.[0] ?? null, prompt: prompt.slice(0, 600) })
    }
    const step = plan?.shift() ?? { text: '(live harness: this main turn was answered locally; no parent model request)' }
    let index = 0
    const answer = step.text ?? ''
    if (answer) { yield { kind: 'text', index, text: answer }; index += 1 }
    const toolUses: { name: string; input: unknown }[] = []
    for (const tool of step.tools ?? []) {
      const input = await resolve($, tool.input)
      yield { kind: 'tool', index, id: 'toolu_01guard' + Math.random().toString(36).slice(2, 12) + index, name: tool.name }
      yield { kind: 'input', index, json: JSON.stringify(input) }
      toolUses.push({ name: tool.name, input })
      index += 1
    }
    const stopReason = toolUses.length ? 'tool_use' : 'end_turn'
    yield { kind: 'stop', stopReason, usage: null }
    return { turnId: e.turnId, index: e.index, answer, toolUses, stopReason, usage: null }
  }).catch(async function* guardFault($, e: TurnStepInput, next): AsyncGenerator<any, TurnStepResult> {
    if (e.agentId !== undefined || turns <= Number((await $.env.get('NP_GUARD_REAL_TURNS')) ?? '1')) return yield* next(e)
    const text = '(live harness guard fault)'
    yield { kind: 'text', index: 0, text };yield { kind: 'stop', stopReason: 'end_turn', usage: null }
    return { turnId: e.turnId, index: e.index, answer: text, toolUses: [], stopReason: 'end_turn', usage: null }
  })
}
