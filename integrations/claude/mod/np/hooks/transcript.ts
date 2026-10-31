// A worker's conversation as the pane shows it, built from its activity log. Pure: no $ here.
import type { NpRow, NpTranscript } from '../types'
import type { Activity } from './mirror'
import { duration } from './mirror'

export const MAX_ROWS = 80
const MAX_TEXT = 3000
const MAX_FULL = 12288
const MAX_OUTPUT = 600

function clip(text: string, limit: number): string {
  return text.length > limit ? text.slice(0, limit) + '…' : text
}

// What a person or the parent wrote, without the routing envelope NativePeers wraps around a follow-up for the worker:
// 'NativePeers · Message', a blank line, the text, then '— NativePeers references —' and its ids.
export function bodyOf(text: string): string {
  const head = /^NativePeers · [^\n]*\n\n/.exec(text)
  if (!head) return text
  const end = text.lastIndexOf('\n\n— NativePeers references —')
  return end > head[0].length ? text.slice(head[0].length, end) : text.slice(head[0].length)
}

// A tool's output as a narrow pane shows it: its first lines, each cut to a width, and how many lines are left out.
export function excerpt(output: string, lines = 6, width = 80): { text: string; more: number } {
  const all = output.split('\n')
  return { text: all.slice(0, lines).map(l => l.length > width ? l.slice(0, width - 1) + '…' : l).join('\n'), more: Math.max(0, all.length - lines) }
}

function same(a: string, b: string): boolean {
  return a.trim().slice(0, MAX_FULL) === b.trim().slice(0, MAX_FULL)
}

// The rows of one worker's conversation after `events`, which continue from `t.cursor`. Item ids repeat across
// generations (Grok streams every message as `text`, Antigravity numbers steps), so rows are keyed by generation too.
// A text row of the running generation keeps its full text, for matching the final answer; it is dropped at the end.
export function applyActivity(t: NpTranscript, events: readonly Activity[]): NpTranscript {
  const rows: NpRow[] = t.rows.map(r => ({ ...r }))
  const find = (item: string) => { for (let n = rows.length - 1;n >= 0;n--) if (rows[n]?.item === item) return rows[n];return undefined }
  const texts = (gen: number) => rows.filter(r => r.kind === 'text' && r.gen === gen)
  const write = (row: NpRow, text: string) => { row.full = text.slice(0, MAX_FULL);row.text = clip(text, MAX_TEXT) }
  // A follow-up's text arrives as input.queued (while the worker runs) and again with its task.started: one row.
  const said = (requestId: unknown, origin: unknown, raw: string, gen: number) => {
    const text = bodyOf(raw)
    if (!text.trim()) return
    const key = typeof requestId === 'string' && requestId ? 'in:' + requestId : undefined
    const held = key ? find(key) : undefined
    if (held) { if (text.length > held.text.length) held.text = clip(text, MAX_TEXT);return }
    rows.push({ kind: origin === 'parent-model' ? 'task' : 'you', text: clip(text, MAX_TEXT), item: key, gen })
  }
  let cursor = t.cursor
  for (const e of events) {
    if (typeof e?.seq !== 'number' || e.seq <= cursor) continue
    cursor = e.seq
    const gen = typeof e.generation === 'number' ? e.generation : 1
    try {
      const d = e.data && typeof e.data === 'object' ? e.data : {}
      switch (e.kind) {
        case 'task.started':
          if (gen <= 1) { if (d.input_preview && !rows.some(r => r.kind === 'task')) rows.push({ kind: 'task', text: clip(bodyOf(String(d.input_preview)), MAX_TEXT), gen }) }
          else said(d.client_request_id, d.origin, String(d.input_preview ?? ''), gen)
          break
        case 'input.queued':
          said(d.client_request_id, d.origin, String(d.preview ?? ''), gen)
          break
        case 'message.delta': {
          const held = e.item ? find(`m:${gen}:${e.item}`) : undefined
          if (held) write(held, (held.full ?? held.text) + String(d.text ?? ''))
          else if (d.text) { const row: NpRow = { kind: 'text', text: '', item: e.item ? `m:${gen}:${e.item}` : undefined, gen };write(row, String(d.text));rows.push(row) }
          break
        }
        case 'message.completed': {
          const text = String(d.text ?? '')
          if (!text.trim()) break
          const held = e.item ? find(`m:${gen}:${e.item}`) : undefined
          if (held) { write(held, text);break }
          // An itemless final candidate repeats what this generation already streamed.
          if (!e.item && texts(gen).some(r => same(r.full ?? r.text, text))) break
          const row: NpRow = { kind: 'text', text: '', item: e.item ? `m:${gen}:${e.item}` : undefined, gen };write(row, text);rows.push(row)
          break
        }
        case 'tool.started':
          rows.push({ kind: 'tool', text: clip(String(d.title ?? d.kind ?? 'tool'), 200), item: `t:${gen}:${e.item ?? e.seq}`, gen })
          break
        case 'tool.completed': {
          const held = find(`t:${gen}:${e.item ?? ''}`)
          if (!held) break
          held.status = d.exit_code !== undefined ? `exit ${d.exit_code}` : String(d.status ?? 'ok')
          if (d.duration_ms !== undefined) held.status += ' · ' + duration(Number(d.duration_ms))
          const output = d.output && typeof d.output === 'object' ? d.output.text : d.output
          if (output) held.detail = clip(String(output).trimEnd(), MAX_OUTPUT)
          break
        }
        case 'files.changed': {
          const paths = Array.isArray(d.paths) ? d.paths.map(String) : []
          rows.push({ kind: 'tool', text: `Edited ${paths.slice(0, 3).join(', ')}${paths.length > 3 ? ` and ${paths.length - 3} more` : ''} (+${d.stats?.added ?? 0} −${d.stats?.removed ?? 0})`, gen })
          break
        }
        case 'plan.updated': {
          const items = Array.isArray(d.items) ? d.items.filter((i: unknown) => i && typeof i === 'object') : []
          if (items.length) rows.push({ kind: 'note', text: 'Plan:\n' + items.map((i: any) => `${i.status === 'completed' ? '✓' : '·'} ${String(i.text ?? '')}`).join('\n'), gen })
          break
        }
        case 'fallback.started':
          rows.push({ kind: 'note', text: `Moved to ${d.to?.provider ?? 'another provider'}${d.to?.model ? ' ' + d.to.model : ''} (${d.reason ?? 'fallback'}).`, gen })
          break
        case 'gap':
          rows.push({ kind: 'note', text: `Some activity is missing here (${d.reason ?? 'unknown'}).`, gen })
          break
        case 'task.terminal': {
          const status = String(d.status ?? 'completed')
          const final = String(d.final_text ?? '')
          if (final.trim() && !texts(gen).some(r => same(r.full ?? r.text, final))) rows.push({ kind: 'text', text: clip(final.trim(), MAX_TEXT), gen })
          const error = d.error?.code ? `: ${d.error.code}${d.error.message ? ' ' + String(d.error.message).slice(0, 200) : ''}` : ''
          rows.push({ kind: 'note', text: `${status.charAt(0).toUpperCase() + status.slice(1).replace(/_/g, ' ')}${error}.`, gen })
          for (const r of texts(gen)) delete r.full
          break
        }
        default:
          break
      }
    } catch {
      rows.push({ kind: 'note', text: 'An activity event could not be shown here.', gen })
    }
  }
  return { ...t, cursor, rows: rows.slice(-MAX_ROWS) }
}
