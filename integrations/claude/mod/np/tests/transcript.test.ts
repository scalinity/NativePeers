import { expect, test } from 'claude-code/testing'
import { MAX_ROWS, applyActivity, bodyOf, excerpt } from '../hooks/transcript'
import type { Activity } from '../hooks/mirror'

const ev = (seq: number, kind: string, data: any = {}, item: string | null = null, generation = 1): Activity => ({ seq, kind, data, item, generation, task_id: 'T' + generation })
const empty = { workerId: 'W1', cursor: 0, rows: [] }

test('a conversation shows the brief, the worker\'s messages, its tools with output, and how it ended', () => {
  const t = applyActivity(empty, [
    ev(1, 'task.started', { origin: 'parent-model', input_preview: 'List the files in this folder.' }),
    ev(2, 'message.delta', { text: 'I will ' }, 'm1'), ev(3, 'message.delta', { text: 'look.' }, 'm1'),
    ev(4, 'tool.started', { kind: 'exec', title: 'ls -A1' }, 'c1'),
    ev(5, 'tool.completed', { status: 'ok', exit_code: 0, duration_ms: 400, output: { text: '.git\nREADME.md\ntally\ntests\n' } }, 'c1'),
    ev(6, 'message.completed', { text: 'It holds .git, README.md, tally and tests.', is_final_candidate: true }, 'm2'),
    ev(7, 'task.terminal', { status: 'completed', final_text: 'It holds .git, README.md, tally and tests.' }),
  ])
  expect(t.cursor).toBe(7)
  expect(t.rows.map(r => r.kind)).toEqual(['task', 'text', 'tool', 'text', 'note'])
  expect(t.rows[1]?.text).toBe('I will look.')
  expect(t.rows[2]).toMatchObject({ text: 'ls -A1', status: 'exit 0 · 0s', detail: '.git\nREADME.md\ntally\ntests' })
  // The final text already shown is not repeated.
  expect(t.rows.filter(r => r.text.includes('It holds')).length).toBe(1)
  expect(t.rows[4]?.text).toBe('Completed.')
})

test('a follow-up shows once, as the person\'s or main\'s, even when it arrives as input.queued and again with its task', () => {
  const start = applyActivity(empty, [ev(1, 'task.started', { input_preview: 'Brief.' }), ev(2, 'task.terminal', { status: 'completed', final_text: 'Done.' })])
  const t = applyActivity(start, [
    ev(3, 'input.queued', { client_request_id: 'np-pane:W1:1', origin: 'human-claude-view', preview: 'Also count th' }),
    ev(4, 'task.started', { client_request_id: 'np-pane:W1:1', origin: 'human-claude-view', input_preview: 'Also count them.' }, null, 2),
    ev(5, 'task.started', { client_request_id: 'r2', origin: 'parent-model', input_preview: 'From main: check tests.' }, null, 3),
  ])
  const you = t.rows.filter(r => r.kind === 'you')
  expect(you.length).toBe(1)
  expect(you[0]?.text).toBe('Also count them.')
  expect(t.rows[t.rows.length - 1]).toMatchObject({ kind: 'task', text: 'From main: check tests.' })
})

test('events at or before the cursor are skipped, and the conversation keeps its newest rows', () => {
  const t = applyActivity({ workerId: 'W1', cursor: 5, rows: [] }, [ev(5, 'message.completed', { text: 'old' }, 'm0'), ev(6, 'message.completed', { text: 'new' }, 'm1')])
  expect(t.rows.map(r => r.text)).toEqual(['new'])
  const many = Array.from({ length: MAX_ROWS + 10 }, (_, n) => ev(n + 1, 'message.completed', { text: 'm' + n }, 'i' + n))
  const capped = applyActivity(empty, many)
  expect(capped.rows.length).toBe(MAX_ROWS)
  expect(capped.rows[capped.rows.length - 1]?.text).toBe('m' + (MAX_ROWS + 9))
})

test('rows are keyed by generation: an item id reused by a follow-up starts a new row after it', () => {
  const t = applyActivity(empty, [
    ev(1, 'message.delta', { text: 'first answer' }, 'text', 1), ev(2, 'task.terminal', { status: 'completed', final_text: 'first answer' }, null, 1),
    ev(3, 'task.started', { client_request_id: 'r1', origin: 'human-claude-view', input_preview: 'And again?' }, null, 2),
    ev(4, 'message.delta', { text: 'second answer' }, 'text', 2), ev(5, 'tool.started', { title: 'ls' }, 'step0', 2), ev(6, 'tool.completed', { status: 'ok' }, 'step0', 2),
  ])
  expect(t.rows.map(r => r.text)).toEqual(['first answer', 'Completed.', 'And again?', 'second answer', 'ls'])
  expect(t.rows[4]?.status).toBe('ok')
})

test('a final answer is matched in full within its own generation, and an itemless final candidate is not repeated', () => {
  const long = 'x'.repeat(4000)
  const t = applyActivity(empty, [ev(1, 'message.completed', { text: long }, 'm1'), ev(2, 'task.terminal', { status: 'completed', final_text: long })])
  expect(t.rows.filter(r => r.kind === 'text').length).toBe(1)
  expect(t.rows[0]?.full).toBe(undefined)
  const streamed = applyActivity(empty, [ev(1, 'message.delta', { text: 'Done.' }, 'text'), ev(2, 'message.completed', { text: 'Done.', is_final_candidate: true })])
  expect(streamed.rows.filter(r => r.kind === 'text').length).toBe(1)
  // The same words in a later generation are its own answer.
  const later = applyActivity(t, [ev(3, 'task.started', { origin: 'human-claude-view', input_preview: 'Repeat it.' }, null, 2), ev(4, 'task.terminal', { status: 'completed', final_text: 'Done.' }, null, 2)])
  expect(later.rows.filter(r => r.kind === 'text' && r.text === 'Done.').length).toBe(1)
})

test('a malformed event becomes a note and the conversation moves on', () => {
  const t = applyActivity(empty, [ev(1, 'plan.updated', { items: [null, { text: 'step', status: 'completed' }] }), ev(2, 'tool.completed', { output: null }, 'nope'),
    { seq: 3, kind: 'message.completed', generation: 1, data: { get text() { throw new Error('bad') } } } as any, ev(4, 'message.completed', { text: 'still here' }, 'm9')])
  expect(t.cursor).toBe(4)
  expect(t.rows.map(r => r.text)).toEqual(['Plan:\n✓ step', 'An activity event could not be shown here.', 'still here'])
})

const ENVELOPE = 'NativePeers · Message\n\ntest; reply with "passed"\n\n— NativePeers references —\nFrom: np1_cf4c\nMessage: 178da578\nExchange: 34b9b65f\nEnvelope: v1 / 7f4c20fa'

test('a follow-up shows what was written, without the routing envelope the worker receives', () => {
  expect(bodyOf(ENVELOPE)).toBe('test; reply with "passed"')
  expect(bodyOf('plain text')).toBe('plain text')
  // A preview cut before the references still loses the heading.
  expect(bodyOf('NativePeers · Reply\n\nshort answer, cut off')).toBe('short answer, cut off')
  const t = applyActivity(empty, [ev(1, 'task.started', { client_request_id: 'r1', origin: 'human-claude-view', input_preview: ENVELOPE }, null, 2)])
  expect(t.rows).toEqual([{ kind: 'you', text: 'test; reply with "passed"', item: 'in:r1', gen: 2 }])
})

test('tool output in the pane is an excerpt: its first lines, each cut to a width, and a count of the rest', () => {
  const out = excerpt(Array.from({ length: 10 }, (_, n) => 'line ' + n + (n === 0 ? ' ' + 'x'.repeat(100) : '')).join('\n'))
  expect(out.text.split('\n').length).toBe(6)
  expect(out.text.split('\n')[0]?.length).toBe(80)
  expect(out.more).toBe(4)
  expect(excerpt('one\ntwo').more).toBe(0)
})
