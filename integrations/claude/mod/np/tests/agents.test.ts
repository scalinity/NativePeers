import { expect, test } from 'claude-code/testing'
import { agentSpec, describe, modelLabel, parseHeader, shadowModel, startedSince, statusLabel, statusSummary, titleFrom, workerSpec } from '../hooks/agents'
import { followUp, handbackRequired, provenance, stripFraming } from '../hooks/inputs'

test('a shadow type has only the mirror tool and SubagentHandback, an unserved model and an explicit permission mode', () => {
  const spec = agentSpec('codex')
  expect(spec.tools).toEqual(['mcp__np__mirror', 'SubagentHandback'])
  expect(spec.model).toBe('np-mirror-codex')
  expect(shadowModel('grok')).toBe('np-mirror-grok')
  expect(spec.permissionMode).toBe('dontAsk')
  expect(spec.background).toBe(true)
  expect(spec.omitClaudeMd).toBe(true)
})

test('the np header is strict JSON with known keys and is stripped from the brief', () => {
  const ok = parseHeader('```np\n{"model":"gpt-6.1-sol","effort":"high","title":"Review"}\n```\nDo the review.')
  expect(ok).toEqual({ header: { model: 'gpt-6.1-sol', effort: 'high', title: 'Review' }, brief: 'Do the review.' })
  expect(parseHeader('Just a brief.')).toEqual({ header: {}, brief: 'Just a brief.' })
  // The forms a model writes from the compose section's one-line example.
  expect(parseHeader('```np {"model":"gpt-6.1-sol","effort":"high"}\n```\nDo it.')).toEqual({ header: { model: 'gpt-6.1-sol', effort: 'high' }, brief: 'Do it.' })
  expect(parseHeader('```np {"effort":"low"}```\nDo it.')).toEqual({ header: { effort: 'low' }, brief: 'Do it.' })
  expect((parseHeader('```np {"effort":"low"}\nDo it.') as any).error).toContain('not closed')
  expect((parseHeader('```np\n{"colour":"red"}\n```\nx') as any).error).toContain('unknown np header key "colour"')
  expect((parseHeader('```np\n{model: x}\n```\nx') as any).error).toContain('not strict JSON')
  expect((parseHeader('```np\n{"profile":"trusted"}\n```\nx') as any).error).toContain('analysis or workspace')
  expect((parseHeader('```np\n{"model":"m"}\n```\n   ') as any).error).toContain('empty')
  expect((parseHeader('```np\n' + JSON.stringify({ title: 'x'.repeat(5000) }) + '\n```\nx') as any).error).toContain('4 KiB')
})

test('labels read like model names', () => {
  expect(modelLabel('gpt-6.1-sol', 'codex')).toBe('GPT-6.1 Sol')
  expect(modelLabel('claude-sonnet-5-5', 'claude')).toBe('Sonnet 5.5')
  expect(modelLabel('grok-4.7', 'grok')).toBe('Grok 4.7')
  expect(modelLabel('gemini-4-argon-high', 'antigravity')).toBe('Gemini 4 Argon high')
  expect(modelLabel(undefined, 'codex')).toBe('codex default')
  expect(describe('codex', 'gpt-6.1-sol', 'high', 'Codebase review')).toBe('GPT-6.1 Sol high · Codebase review')
  expect(titleFrom({}, '  Review   the code ', 'brief')).toBe('Review the code')
})

test('worker specs default to the read-only analysis profile and never invent a model', () => {
  expect(workerSpec('codex', {}, 'b', 't', '/w')).toEqual({ provider: 'codex', title: 't', task: 'b', permission_profile: 'analysis', workspace: { mode: 'inherit-read-only' }, cwd: '/w' })
  expect(workerSpec('claude', { profile: 'workspace', model: 'sonnet', effort: 'high' }, 'b', 't', undefined))
    .toEqual({ provider: 'claude', title: 't', task: 'b', permission_profile: 'workspace', workspace: { mode: 'isolated-worktree' }, model: 'sonnet', reasoning_effort: 'high' })
})

test('engine framing is stripped from typed and parent follow-ups (gate G3)', () => {
  expect(stripFraming('<system-reminder>\nThe user sent a new message while you were working:\nalso check the tests\n\nThis is how Claude Code surfaces messages the user sends mid-turn — within the running turn. Address the message above.\n</system-reminder>')).toBe('also check the tests')
  expect(stripFraming('The user sent a new message while you were working:\nsecond\n\nThis is how Claude Code surfaces messages the user sends mid-turn, rather than as a separate turn.')).toBe('second')
  expect(stripFraming('The coordinator sent a message while you were working:\nfollow up\n\nAddress this before completing your current task.')).toBe('follow up')
  expect(stripFraming('plain text')).toBe('plain text')
})

test('only prompt and delivery rows from a person or the parent are follow-ups', () => {
  const row = (door: string, kind: string, content: any, type = 'user') => ({ door, origin: { kind }, message: { type, content } })
  expect(followUp(row('prompt', 'composer', [{ type: 'text', text: 'hi' }]))).toEqual({ text: 'hi', origin: 'human-claude-view' })
  expect(followUp(row('delivery', 'composer', [{ type: 'text', text: 'hi' }], 'attachment'))).toEqual({ text: 'hi', origin: 'human-claude-view' })
  expect(followUp(row('prompt', 'coordinator', [{ type: 'text', text: 'go' }]))).toEqual({ text: 'go', origin: 'parent-model' })
  expect(followUp(row('queue', 'composer', [{ type: 'text', text: 'hi' }]))).toBe(null)
  expect(followUp(row('tool-result', 'tool', [{ type: 'tool_result', content: 'x' }]))).toBe(null)
  expect(followUp(row('prompt', 'engine', [{ type: 'text', text: 'x' }]))).toBe(null)
  expect(provenance('model')).toBe('parent-model')
  expect(provenance('task-notification')).toBe(null)
})

// The rows a shadow saw in an auto-mode session (gate G6 capture, desktop run 5a5bfe52).
const NOTE = '<system-reminder>\nYour final report is delivered through SubagentHandback: when your work is complete, call SubagentHandback({message: <your full report>}). The call ends your run, so make it your last step.\n</system-reminder>'
const ENFORCE = '[handback-send-enforce] Your report has not been delivered. Call SubagentHandback({message: <your full report>}) now; the call ends your run.'

test('the engine\'s SubagentHandback note and enforcement mark a run that must hand back; nobody else\'s text does', () => {
  const row = (text: string, kind: string, door = 'note') => ({ door, origin: { kind }, message: { type: 'user', content: [{ type: 'text', text }] } })
  expect(handbackRequired(row(NOTE, 'engine'))).toBe(true)
  expect(handbackRequired(row(ENFORCE, 'engine', 'prompt'))).toBe(true)
  expect(handbackRequired({ door: 'prompt', origin: { kind: 'engine' }, message: { type: 'user', content: ENFORCE } })).toBe(true)
  expect(handbackRequired(row(NOTE, 'composer', 'prompt'))).toBe(false)
  expect(handbackRequired(row(NOTE, 'coordinator', 'delivery'))).toBe(false)
  expect(handbackRequired(row('Your report is ready.', 'engine'))).toBe(false)
  // Neither row is ever forwarded to the worker as a follow-up.
  expect(followUp(row(NOTE, 'engine'))).toBe(null)
  expect(followUp(row(ENFORCE, 'engine', 'prompt'))).toBe(null)
})

test('the status line counts what delegates are doing and stays until there are none', () => {
  const w = (status: string, parentWorkerId?: string) => ({ workerId: status, groupId: 'G', generation: 1, provider: 'codex', title: 't', status, parentWorkerId }) as any
  expect(statusSummary([])).toBe(undefined)
  expect(statusSummary([w('running')])).toBe('1 running')
  expect(statusSummary([w('completed')])).toBe('1 done')
  expect(statusSummary([w('running'), w('completed'), w('failed'), w('canceled'), w('running', 'P')])).toBe('1 running · 1 done · 1 failed · 1 stopped')
  expect(statusLabel('timed_out')).toBe('Timed out')
  expect(statusLabel('completed')).toBe('Completed')
})


test('a worker counts as started once, when it is first seen running, however it was started', () => {
  const seen = new Set<string>()
  const ws = [{ workerId: 'W1', status: 'running' }, { workerId: 'W2', status: 'completed' }] as any
  expect(startedSince(seen, ws).map((w: any) => w.workerId)).toEqual(['W1'])
  expect(startedSince(seen, ws)).toEqual([])
  expect(startedSince(seen, [...ws, { workerId: 'W3', status: 'queued' }]).map((w: any) => w.workerId)).toEqual(['W3'])
})
