import { expect, test } from 'claude-code/testing'
import { MAX_ROWS, applyActivity } from '../hooks/transcript'
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
