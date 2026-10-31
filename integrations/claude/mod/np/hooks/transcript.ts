// A worker's conversation as the pane shows it, built from its activity log. Pure: no $ here.
import type { NpRow, NpTranscript } from '../types'
import type { Activity } from './mirror'
import { duration } from './mirror'

export const MAX_ROWS = 80
const MAX_TEXT = 3000
const MAX_OUTPUT = 600

function clip(text: string, limit: number): string {
  return text.length > limit ? text.slice(0, limit) + '…' : text
}

// The rows of one worker's conversation after `events`, which continue from `t.cursor`.
export function applyActivity(t: NpTranscript, events: readonly Activity[]): NpTranscript {
  const rows = t.rows.map(r => ({ ...r }))
  const find = (item: string) => { for (let n = rows.length - 1;n >= 0;n--) if (rows[n]?.item === item) return rows[n];return undefined }
  // A follow-up's text arrives as input.queued (while the worker runs) and again with its task.started: one row.
  const said = (requestId: string | null | undefined, origin: string | undefined, text: string) => {
    if (!text.trim()) return
    const key = requestId ? 'in:' + requestId : undefined
    const held = key ? find(key) : undefined
    if (held) { if (text.length > held.text.length) held.text = clip(text, MAX_TEXT);return }
    rows.push({ kind: origin === 'parent-model' ? 'task' : 'you', text: clip(text, MAX_TEXT), item: key })
  }
  let cursor = t.cursor
  for (const e of events) {
    if (e.seq <= cursor) continue
    cursor = e.seq
    const d = e.data ?? {}
    switch (e.kind) {
      case 'task.started':
        if (e.generation <= 1) { if (d.input_preview && !rows.some(r => r.kind === 'task')) rows.push({ kind: 'task', text: clip(String(d.input_preview), MAX_TEXT) }) }
        else said(d.client_request_id, d.origin, String(d.input_preview ?? ''))
        break
      case 'input.queued':
        said(d.client_request_id, d.origin, String(d.preview ?? ''))
        break
      case 'message.delta': {
        const held = e.item ? find('m:' + e.item) : undefined
        if (held) held.text = clip(held.text + String(d.text ?? ''), MAX_TEXT)
        else if (d.text) rows.push({ kind: 'text', text: clip(String(d.text), MAX_TEXT), item: e.item ? 'm:' + e.item : undefined })
        break
      }
      case 'message.completed': {
        const held = e.item ? find('m:' + e.item) : undefined
        if (held) held.text = clip(String(d.text ?? held.text), MAX_TEXT)
        else if (d.text) rows.push({ kind: 'text', text: clip(String(d.text), MAX_TEXT), item: e.item ? 'm:' + e.item : undefined })
        break
      }
      case 'tool.started':
        rows.push({ kind: 'tool', text: clip(String(d.title ?? d.kind ?? 'tool'), 200), item: 't:' + (e.item ?? e.seq) })
        break
      case 'tool.completed': {
        const held = find('t:' + (e.item ?? ''))
        if (!held) break
        held.status = d.exit_code !== undefined ? `exit ${d.exit_code}` : String(d.status ?? 'ok')
        if (d.duration_ms !== undefined) held.status += ' · ' + duration(d.duration_ms)
        const output = typeof d.output === 'object' ? d.output?.text : d.output
        if (output) held.detail = clip(String(output).trimEnd(), MAX_OUTPUT)
        break
      }
      case 'files.changed': {
        const paths: string[] = Array.isArray(d.paths) ? d.paths : []
        rows.push({ kind: 'tool', text: `Edited ${paths.slice(0, 3).join(', ')}${paths.length > 3 ? ` and ${paths.length - 3} more` : ''} (+${d.stats?.added ?? 0} −${d.stats?.removed ?? 0})` })
        break
      }
      case 'plan.updated': {
        const items: any[] = Array.isArray(d.items) ? d.items : []
        rows.push({ kind: 'note', text: 'Plan:\n' + items.map(i => `${i.status === 'completed' ? '✓' : '·'} ${String(i.text ?? '')}`).join('\n') })
        break
      }
      case 'fallback.started':
        rows.push({ kind: 'note', text: `Moved to ${d.to?.provider ?? 'another provider'}${d.to?.model ? ' ' + d.to.model : ''} (${d.reason ?? 'fallback'}).` })
        break
      case 'gap':
        rows.push({ kind: 'note', text: `Some activity is missing here (${d.reason ?? 'unknown'}).` })
        break
      case 'task.terminal': {
        const status = String(d.status ?? 'completed')
        const final = String(d.final_text ?? '').trim()
        if (final && !rows.some(r => r.kind === 'text' && r.text.trim() === final)) rows.push({ kind: 'text', text: clip(final, MAX_TEXT) })
        const error = d.error?.code ? `: ${d.error.code}${d.error.message ? ' ' + String(d.error.message).slice(0, 200) : ''}` : ''
        rows.push({ kind: 'note', text: `${status.charAt(0).toUpperCase() + status.slice(1).replace(/_/g, ' ')}${error}.` })
        break
      }
      default:
        break
    }
  }
  return { ...t, cursor, rows: rows.slice(-MAX_ROWS) }
}
