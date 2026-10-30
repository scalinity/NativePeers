import type { Register, EngineInterface, TurnStepInput, TurnStepResult } from 'claude-code'

// Harness-only driver. Main-loop steps are answered from the JSON script named
// by NP_DRIVER_SCRIPT:
//   { "steps": [Step, ...],            consumed in order by turns no rule picks
//     "on": { "<prompt substring>": [Step, ...] },  a turn whose prompt holds the
//                                                    substring runs that plan
//     "notifications": "ack" }          a task-notification turn ends at once
// Step = { "text"?, "tools"?: [{ "name", "input" }], "sleep_ms"? }. A step with
// tools stops for tool_use; one without ends the turn. "${DIR}" in any string
// becomes NP_FIXTURE_DIR, "${agent:TYPE}" the newest agent id of that type and
// "${agent1:TYPE}" the oldest.
type Step = { text?: string; tools?: { name: string; input: unknown }[]; sleep_ms?: number }
type Script = { steps?: Step[]; on?: Record<string, Step[]>; notifications?: 'ack' }

let script: Script | undefined
let cursor = 0
let plan: Step[] | undefined
let acks = 0

async function loadScript($: EngineInterface): Promise<Script> {
  if (script) return script
  const path = await $.env.get('NP_DRIVER_SCRIPT')
  script = path ? JSON.parse(await $.fs.read(path)) : {}
  return script!
}

async function substitute($: EngineInterface, value: unknown): Promise<unknown> {
  if (typeof value === 'string') {
    let out = value
    if (out.includes('${DIR}')) out = out.split('${DIR}').join((await $.env.get('NP_FIXTURE_DIR')) ?? '')
    const match = /\$\{agent(1?):([^}]+)\}/.exec(out)
    if (match) {
      const agents = (await $.agent.list()).filter(a => a.type === match[2])
      const pick = match[1] === '1' ? agents[0] : agents[agents.length - 1]
      out = out.replace(match[0], pick ? pick.id : 'missing-agent')
    }
    return out
  }
  if (Array.isArray(value)) { const out = []; for (const v of value) out.push(await substitute($, v)); return out }
  if (value && typeof value === 'object') {
    const out: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(value)) out[k] = await substitute($, v)
    return out
  }
  return value
}

async function planStep($: EngineInterface, e: TurnStepInput): Promise<Step> {
  const s = await loadScript($)
  if (e.index === 0) {
    plan = undefined
    const messages = await $.session.messages()
    const prompt = String(messages.filter((m: any) => m.role === 'user').slice(-1)[0]?.text ?? '')
    if (s.notifications === 'ack' && prompt.includes('<task-notification>')) { acks += 1; plan = [{ text: `main: ack ${acks}` }] }
    for (const [key, steps] of Object.entries(s.on ?? {})) if (!plan && prompt.includes(key)) plan = [...steps]
  }
  let step: Step | undefined
  if (plan) step = plan.shift()
  else { step = (s.steps ?? [])[cursor]; cursor += 1 }
  step = step ?? { text: 'driver: script exhausted' }
  if (step.sleep_ms) await $.process.run(['sleep', String(step.sleep_ms / 1000)], { timeoutMs: step.sleep_ms + 5000 })
  return (await substitute($, step)) as Step
}

function toolId(n: number): string {
  return 'toolu_01drv' + String(Date.now()).slice(-8) + String(n).padStart(4, '0') + Math.random().toString(36).slice(2, 8)
}

export const register: Register = on => {
  on('turn.step', async function* driverStep($, e: TurnStepInput, next): AsyncGenerator<any, TurnStepResult> {
    if (e.agentId !== undefined) return yield* next(e)
    const step = await planStep($, e)
    let index = 0
    const answer = step.text ?? ''
    if (answer) { yield { kind: 'text', index, text: answer }; index += 1 }
    const toolUses = []
    for (const tool of step.tools ?? []) {
      yield { kind: 'tool', index, id: toolId(index), name: tool.name }
      yield { kind: 'input', index, json: JSON.stringify(tool.input) }
      toolUses.push({ name: tool.name, input: tool.input })
      index += 1
    }
    const stopReason = toolUses.length ? 'tool_use' : 'end_turn'
    yield { kind: 'stop', stopReason, usage: null }
    return { turnId: e.turnId, index: e.index, answer, toolUses, stopReason, usage: null }
  }).catch(async function* driverFault($, e, next): AsyncGenerator<any, TurnStepResult> {
    const text = `driver fault: ${next.error?.kind ?? 'unknown'}`
    yield { kind: 'text', index: 0, text }
    yield { kind: 'stop', stopReason: 'end_turn', usage: null }
    return { turnId: e.turnId, index: e.index, answer: text, toolUses: [], stopReason: 'end_turn', usage: null }
  })
}
