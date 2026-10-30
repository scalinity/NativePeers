import { expect, test } from 'claude-code/testing'
import { agentSpec, describe, modelLabel, parseHeader, shadowModel, titleFrom, workerSpec } from '../hooks/agents.ts'
import { followUp, provenance, stripFraming } from '../hooks/inputs.ts'

test('a shadow type has only the mirror tool, an unserved model and an explicit permission mode', () => {
  const spec = agentSpec('codex')
  expect(spec.tools).toEqual(['mcp__np__mirror'])
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
