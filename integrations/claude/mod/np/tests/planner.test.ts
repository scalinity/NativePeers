import { expect, test } from 'claude-code/testing'
import { MIRROR_TOOL, closingLine, finalAnswer, openStep, planBatch, toolResult } from '../hooks/mirror'
import type { Activity } from '../hooks/mirror'

let n = 0
const ctx = (continues = false) => ({ workerId: 'W', showReasoning: true, newId: () => 'toolu_' + (++n), continuesAfter: () => continues })
const ev = (seq: number, kind: string, data: any = {}, item: string | null = null, generation = 1): Activity => ({ seq, kind, data, item, generation, task_id: 'T' + generation })

test('streamed deltas join one text block and a message already streamed is not repeated', () => {
  const open = openStep()
  const p = planBatch([ev(1, 'message.delta', { text: 'Hello ' }, 'm1'), ev(2, 'message.delta', { text: 'world' }, 'm1'), ev(3, 'message.completed', { text: 'Hello world' }, 'm1')], open, ctx())
  expect(p.chunks).toEqual([{ kind: 'text', index: 0, text: 'Hello ' }, { kind: 'text', index: 0, text: 'world' }])
  expect(p.consumed).toBe(3)
  expect(p.end).toBe(null)
  expect(open.answer).toBe('Hello world')
})

test('a tool start ends the step with a mirror call: title first, a short ref, no detail that repeats the title', () => {
  const p = planBatch([ev(1, 'message.completed', { text: 'Running tests.' }, 'm1'), ev(2, 'tool.started', { kind: 'exec', title: 'pytest -q', input: { command: 'pytest -q' } }, 'c1'), ev(3, 'message.delta', { text: 'after' }, 'm2')], openStep(), ctx())
  expect(p.end).toBe('tool_use')
  expect(p.consumed).toBe(2)
  const tool = p.chunks.find(c => c.kind === 'tool') as any
  expect(tool.name).toBe(MIRROR_TOOL)
  const input = JSON.parse((p.chunks.find(c => c.kind === 'input') as any).json)
  expect(input).toEqual({ title: 'pytest -q', kind: 'exec', ref: '#2' })
  expect(Object.keys(input)[0]).toBe('title')
  expect(p.refs['#2']).toMatchObject({ item: 'c1', kind: 'exec', startSeq: 2, generation: 1, title: 'pytest -q', detail: '' })
  expect(p.toolUses).toEqual([{ name: MIRROR_TOOL, input }])
})

test('parallel tool starts share one step, at most eight, and their completions are consumed', () => {
  const events = Array.from({ length: 10 }, (_, i) => ev(i + 1, 'tool.started', { kind: 'read', title: 'f' + i, input: { path: 'f' + i } }, 'r' + i))
  events.splice(2, 0, ev(99, 'tool.completed', { status: 'ok' }, 'r0'))
  const p = planBatch(events, openStep(), ctx())
  expect(p.toolUses.length).toBe(8)
  expect(p.end).toBe('tool_use')
})

test('a final candidate equal to the terminal text is shown once, in the final answer', () => {
  const open = openStep()
  const p = planBatch([ev(1, 'message.completed', { text: 'Done: A, B.', is_final_candidate: true }, 'm1'), ev(2, 'task.terminal', { status: 'completed', final_text: 'Done: A, B.' })], open, ctx())
  expect(p.end).toBe('terminal')
  expect(p.chunks).toEqual([])
  expect(p.terminal?.seq).toBe(2)
})

test('a final candidate that differs from the terminal text is flushed before the answer', () => {
  const p = planBatch([ev(1, 'message.completed', { text: 'Interim.', is_final_candidate: true }, 'm1'), ev(2, 'task.terminal', { status: 'completed', final_text: 'Final.' })], openStep(), ctx())
  expect(p.chunks).toEqual([{ kind: 'text', index: 0, text: 'Interim.' }])
})

test('a terminal followed by a forwarded follow-up continues the same run with a marker', () => {
  const p = planBatch([ev(1, 'task.terminal', { status: 'completed', final_text: 'one' }), ev(2, 'task.started', { origin: 'human-claude-view' }, null, 2)], openStep(), ctx(true))
  expect(p.end).toBe(null)
  const text = p.chunks.map(c => (c as any).text).join('')
  expect(text).toContain('generation 1 completed; continuing')
  expect(text).toContain('generation 2: your follow-up')
  expect(p.folded.map(e => e.generation)).toEqual([1])
})

test('generations folded into one run all reach its final answer, which the one notification carries', () => {
  const terminal = ev(9, 'task.terminal', { status: 'completed', final_text: 'two' }, null, 2)
  const text = finalAnswer(terminal, { provider: 'codex', workerId: 'W' }, 'mirror', [1, 2], true, [{ generation: 1, text: 'one' }])
  expect(text.indexOf('[generation 1]\none')).toBe(0)
  expect(text).toContain('[generation 2]\ntwo')
  expect(text).toContain('Generations: 1, 2')
})

test('gaps, fallbacks, plans and inputs are marked, never silent', () => {
  const p = planBatch([ev(1, 'gap', { reason: 'unmapped', from_seq: 4 }), ev(2, 'fallback.started', { from: { provider: 'claude' }, to: { provider: 'antigravity', model: 'gemini-4-argon-high' }, reason: 'QUOTA_EXHAUSTED' }),
    ev(3, 'plan.updated', { items: [{ text: 'a', status: 'completed' }, { text: 'b', status: 'pending' }] }), ev(4, 'input.queued', { mode: 'queue' })], openStep(), ctx())
  const text = p.chunks.map(c => (c as any).text).join('')
  expect(text).toContain('[activity unavailable from seq 4: unmapped]')
  expect(text).toContain('provider fallback: claude → antigravity gemini-4-argon-high (QUOTA_EXHAUSTED)')
  expect(text).toContain('- [x] a')
  expect(text).toContain('queued for the worker')
})

test('reasoning streams as thinking only when enabled; usage reports the cumulative delta', () => {
  const p = planBatch([ev(1, 'reasoning.delta', { text: 'hm' }), ev(2, 'usage', { output_tokens: 1500, cumulative: true })], openStep(), ctx(), 1000)
  expect(p.chunks).toEqual([{ kind: 'thinking', index: 0, text: 'hm' }])
  expect(p.usage).toEqual({ delta: 500, cumulative: 1500 })
  const off = planBatch([ev(1, 'reasoning.delta', { text: 'hm' })], openStep(), { ...ctx(), showReasoning: false })
  expect(off.chunks).toEqual([])
})

test('a file change with no tool event becomes an immediate edit row', () => {
  const p = planBatch([ev(7, 'files.changed', { paths: ['a.py', 'b.py'], stats: { added: 3, removed: 1 }, diff: { text: '@@' } })], openStep(), ctx())
  expect(p.end).toBe('tool_use')
  expect(p.refs['#7']).toMatchObject({ kind: 'edit', immediate: true })
})

test('tool results replay output, exit, duration and diffs; a terminal without completion is interrupted', () => {
  const pending = { item: 'c1', kind: 'exec', startSeq: 2, title: 'pytest', detail: 'pytest', generation: 1 }
  const ok = toolResult('codex', pending, [ev(3, 'tool.completed', { status: 'ok', exit_code: 0, duration_ms: 90000, output: { text: '42 passed', truncated: false } }, 'c1')], 100)
  expect(ok.text).toBe('[codex exec] exit 0 · 1m30s\n42 passed')
  const cut = toolResult('codex', pending, [ev(3, 'tool.completed', { status: 'ok', output: { text: 'x'.repeat(500), truncated: true, bytes: 900, artifact_id: 'A1' } }, 'c1')], 40)
  expect(cut.text).toContain('[full output: npeer artifact-read A1]')
  expect(cut.text).toContain('…')
  expect(toolResult('codex', pending, [ev(4, 'task.terminal', { status: 'interrupted' })], 100).status).toBe('interrupted')
})

test('the final answer closes in words with one handle, the group, and says who delivered it', () => {
  const terminal = ev(9, 'task.terminal', { status: 'failed', final_text: 'partial', error: { code: 'QUOTA_EXHAUSTED', message: 'limit' } })
  const text = finalAnswer(terminal, { provider: 'codex', modelRequested: 'gpt-6.1-sol', modelObserved: 'gpt-6.1-sol', effort: 'high', profile: 'analysis', workerId: 'W', groupId: 'G', taskId: 'T', nativeId: 'N', elapsedMs: 161000, resultArtifact: 'R' }, 'native', [1])
  expect(text).toContain('— NativePeers · Codex · GPT-6.1 Sol · high effort · read-only · failed in 2m41s')
  expect(text).toContain('Error: QUOTA_EXHAUSTED limit')
  expect(text).toContain('Delivered by NativePeers · npeer result G')
  // No worker, task, native or artifact ids: npeer result G names them.
  for (const id of [' W ', ' T ', ' N ', ' R ', 'worker W', 'native N']) expect(text.includes(id)).toBe(false)
  expect(finalAnswer(terminal, { provider: 'codex', workerId: 'W', groupId: 'G' }, 'native', [1], false, [], 'wait')).toContain('Kept in NativePeers: npeer result G')
  expect(finalAnswer(terminal, { provider: 'codex', workerId: 'W', groupId: 'G' }, 'mirror', [1])).toContain('Details: npeer result G')
  expect(finalAnswer(terminal, { provider: 'codex', workerId: 'W' }, 'mirror', [1], true).startsWith('partial')).toBe(false)
})

test('a detail that adds to the title is kept, and a long title is recorded as the call shows it', () => {
  const long = 'x'.repeat(250)
  const p = planBatch([ev(4, 'tool.started', { kind: 'exec', title: long, input: { command: 'make test' } }, 'c9')], openStep(), ctx())
  const input = JSON.parse((p.chunks.find(c => c.kind === 'input') as any).json)
  expect(input.title.length).toBe(200)
  expect(input.detail).toBe('make test')
  // The mirror tool compares the call's title with the recorded one: both are the 200-character form.
  expect(p.refs['#4']?.title).toBe(input.title)
})

test('the closing line names the worker in words, and the model asked for when another answered', () => {
  const terminal = ev(9, 'task.terminal', { status: 'completed', final_text: 'Done.' })
  expect(closingLine(terminal, { provider: 'codex', modelRequested: 'gpt-6.1-sol', modelObserved: 'gpt-6.1-sol', effort: 'low', profile: 'analysis', workerId: 'W', elapsedMs: 5000 }))
    .toBe('— NativePeers · Codex · GPT-6.1 Sol · low effort · read-only · completed in 5s')
  expect(closingLine(terminal, { provider: 'claude', modelRequested: 'claude-opus-5-5', modelObserved: 'claude-sonnet-5-5', workerId: 'W' })).toContain('Sonnet 5.5 (asked for Opus 5.5)')
  expect(closingLine(ev(9, 'task.terminal', { status: 'timed_out' }), { provider: 'grok', workerId: 'W' })).toBe('— NativePeers · Grok · timed out')
})
