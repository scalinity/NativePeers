// The mirror planner: (activity batch, open step) -> chunks, tool refs and where
// the step ends. Pure: no $ here, so every rule below is unit-tested.
import type { NpPending, NpProvider } from '../types'
import { NAMES, modelLabel } from './agents'

export type Activity = { seq: number; kind: string; generation: number; task_id?: string | null; item?: string | null; data: any; at?: string }
export type Chunk =
  | { kind: 'text'; index: number; text: string }
  | { kind: 'thinking'; index: number; text: string }
  | { kind: 'tool'; index: number; id: string; name: string }
  | { kind: 'input'; index: number; json: string }

export const MIRROR_TOOL = 'mcp__np__mirror'
export const MAX_TOOLS = 8
export const MAX_EVENTS = 512
export const MAX_TEXT = 262144

// What one step has yielded so far, carried across the batches of that step.
export type Open = { block: number; textBlock: number | null; answer: string; streamed: Record<string, true>; tools: number; held?: string; chars: number }
export function openStep(): Open {
  return { block: 0, textBlock: null, answer: '', streamed: {}, tools: 0, chars: 0 }
}

export type Context = {
  workerId: string
  showReasoning: boolean
  newId: () => string
  // True while a forwarded follow-up will start a generation after `generation`.
  continuesAfter: (generation: number) => boolean
}

export type Planned = {
  chunks: Chunk[]
  consumed: number | null
  end: null | 'tool_use' | 'terminal'
  refs: Record<string, NpPending>
  toolUses: { name: string; input: unknown }[]
  terminal?: Activity
  folded: Activity[]
  usage: { delta: number; cumulative?: number }
}

const SKIP_AFTER_TOOL = new Set(['tool.progress', 'tool.completed', 'usage'])

// A shadow's pending refs are its own, so the event's seq names one: short, since the desktop shows the tool's input.
export function ref(seq: number): string {
  return '#' + seq
}

function detailOf(kind: string, input: any): string {
  if (!input || typeof input !== 'object') return String(input ?? '').slice(0, 300)
  if (typeof input.command === 'string') return input.command.slice(0, 300)
  if (Array.isArray(input.command)) return input.command.join(' ').slice(0, 300)
  for (const key of ['file_path', 'path', 'pattern', 'query', 'url'])
    if (typeof input[key] === 'string') return input[key].slice(0, 300)
  return JSON.stringify(input).slice(0, 300)
}

export function planBatch(events: readonly Activity[], open: Open, ctx: Context, lastCumulative?: number): Planned {
  const out: Planned = { chunks: [], consumed: null, end: null, refs: {}, toolUses: [], usage: { delta: 0 }, folded: [] }
  const text = (s: string) => {
    if (!s) return
    if (open.textBlock === null) {
      open.textBlock = open.block++
      if (open.answer && !open.answer.endsWith('\n')) s = '\n\n' + s
    }
    out.chunks.push({ kind: 'text', index: open.textBlock, text: s })
    open.answer += s;open.chars += s.length
  }
  const flushHeld = () => {
    if (open.held !== undefined) { const held = open.held;open.held = undefined;text(held) }
  }
  const marker = (s: string) => { flushHeld();open.textBlock = null;text(s + '\n');open.textBlock = null }
  // The input is what a surface shows for the call: the title first, a detail only when it adds to the title.
  const tool = (kind: string, title: string, detail: string, r: string, pending: NpPending) => {
    flushHeld();open.textBlock = null
    const shownTitle = title.slice(0, 200), shownDetail = detail && detail !== shownTitle ? detail : ''
    const index = open.block++, id = ctx.newId(), input = { title: shownTitle, kind, ...(shownDetail ? { detail: shownDetail } : {}), ref: r }
    out.chunks.push({ kind: 'tool', index, id, name: MIRROR_TOOL }, { kind: 'input', index, json: JSON.stringify(input) })
    out.toolUses.push({ name: MIRROR_TOOL, input });out.refs[r] = { ...pending, title: shownTitle, detail: shownDetail };open.tools += 1
  }
  for (const e of events.slice(0, MAX_EVENTS)) {
    if (open.tools > 0 && e.kind !== 'tool.started' && !SKIP_AFTER_TOOL.has(e.kind)) break
    if (open.tools >= MAX_TOOLS && e.kind === 'tool.started') break
    if (open.chars > MAX_TEXT && open.tools === 0 && out.chunks.length) break
    const d = e.data ?? {}
    switch (e.kind) {
      case 'message.delta':
        flushHeld();if (e.item) open.streamed[e.item] = true;text(String(d.text ?? ''));break
      case 'message.completed':
        if (e.item && open.streamed[e.item]) break
        if (d.is_final_candidate) { flushHeld();open.held = String(d.text ?? '') }
        else { flushHeld();open.textBlock = null;text(String(d.text ?? '')) }
        break
      case 'reasoning.delta':
        if (ctx.showReasoning && d.text) { flushHeld();open.textBlock = null;out.chunks.push({ kind: 'thinking', index: open.block++, text: String(d.text) }) }
        break
      case 'tool.started': {
        const item = e.item ?? 'seq' + e.seq
        const title = String(d.title ?? d.kind ?? 'tool'), kind = String(d.kind ?? 'other')
        tool(kind, title, detailOf(kind, d.input), ref(e.seq), { item, kind, startSeq: e.seq, title, detail: detailOf(kind, d.input), generation: e.generation })
        break
      }
      case 'files.changed':
        if (!e.item) {
          const paths: string[] = Array.isArray(d.paths) ? d.paths : []
          const title = `${paths.slice(0, 3).join(', ')}${paths.length > 3 ? ` +${paths.length - 3}` : ''} (+${d.stats?.added ?? 0} −${d.stats?.removed ?? 0})`
          const item = 'seq' + e.seq
          tool('edit', title, paths.join(', ').slice(0, 300), ref(e.seq), { item, kind: 'edit', startSeq: e.seq, title, detail: paths.join(', ').slice(0, 300), generation: e.generation, immediate: true })
        }
        break
      case 'plan.updated': {
        const items: any[] = Array.isArray(d.items) ? d.items : []
        marker('↳ plan\n' + items.map(i => `- [${i.status === 'completed' ? 'x' : ' '}] ${String(i.text ?? '')}`).join('\n'))
        break
      }
      case 'usage': {
        const n = Number(d.output_tokens ?? 0)
        if (d.cumulative) { const base = out.usage.cumulative ?? lastCumulative ?? 0;if (n > base) out.usage.delta += n - base;out.usage.cumulative = Math.max(n, base) }
        else if (n > 0) out.usage.delta += n
        break
      }
      case 'input.queued':
        marker(d.mode === 'steer' ? '↳ follow-up steered into the running turn' : d.mode === 'resume' ? '↳ follow-up resumes the worker' : '↳ follow-up queued for the worker (after its current turn)')
        break
      case 'input.accepted':
        marker('↳ follow-up accepted');break
      case 'task.started':
        if (e.generation > 1) marker(`↳ generation ${e.generation}: ${d.origin === 'human-claude-view' || d.origin === 'human' ? 'your follow-up' : d.origin === 'parent-model' ? 'follow-up from main' : 'follow-up'}`)
        break
      case 'task.state':
        if (d.state === 'APPROVAL_BLOCKED' || d.state === 'WAITING') marker(`↳ worker ${String(d.state).toLowerCase().replace('_', ' ')}${d.reason ? ': ' + d.reason : ''}`)
        break
      case 'fallback.started':
        marker(`↳ provider fallback: ${d.from?.provider ?? '?'} → ${d.to?.provider ?? '?'} ${d.to?.model ?? ''} (${d.reason ?? 'fallback'})`.replace('  ', ' '))
        break
      case 'gap':
        marker(`[activity unavailable${d.from_seq !== undefined ? ` from seq ${d.from_seq}` : ''}: ${d.reason ?? 'unknown'}]`);break
      case 'task.terminal':
        if (ctx.continuesAfter(e.generation)) {
          flushHeld();marker(`↳ generation ${e.generation} ${d.status ?? 'ended'}; continuing`);out.folded.push(e)
        } else {
          if (open.held !== undefined && open.held.trim() !== String(d.final_text ?? '').trim()) flushHeld()
          open.held = undefined;out.end = 'terminal';out.terminal = e
        }
        break
      default:
        break
    }
    out.consumed = e.seq
    if (out.end) break
  }
  if (!out.end && open.tools > 0) out.end = 'tool_use'
  return out
}

export function duration(ms: number | undefined): string {
  if (ms === undefined || !Number.isFinite(ms) || ms < 0) return ''
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m${String(s % 60).padStart(2, '0')}s`
  return `${Math.floor(s / 3600)}h${String(Math.floor(s / 60) % 60).padStart(2, '0')}m`
}

export function headTail(text: string, limit: number): { text: string; truncated: boolean } {
  if (text.length <= limit) return { text, truncated: false }
  const half = Math.max(0, Math.floor((limit - 3) / 2))
  return { text: text.slice(0, half) + '\n…\n' + text.slice(text.length - half), truncated: true }
}

// The mirror tool's result for one item: started, completed and any file change.
export function toolResult(provider: string, pending: NpPending, events: readonly Activity[], limit: number): { text: string; status: string } {
  const done = events.find(e => e.kind === 'tool.completed' && (e.item ?? '') === pending.item)
  const files = events.filter(e => e.kind === 'files.changed' && ((e.item ?? 'seq' + e.seq) === pending.item))
  const terminal = events.find(e => e.kind === 'task.terminal')
  const d = done?.data ?? {}
  const status = done ? String(d.status ?? 'ok') : pending.immediate ? 'ok' : terminal ? 'interrupted' : 'running'
  const head = [`[${provider} ${pending.kind}]`, status === 'ok' && d.exit_code !== undefined ? `exit ${d.exit_code}` : status,
    duration(d.duration_ms)].filter(Boolean).join(' · ').replace('] · ', '] ')
  const lines = [head]
  const output = d.output?.text ? headTail(String(d.output.text), limit) : null
  if (output?.text) lines.push(output.text)
  for (const f of files) {
    const fd = f.data ?? {}
    lines.push(`${(fd.paths ?? []).join(', ')} (+${fd.stats?.added ?? 0} −${fd.stats?.removed ?? 0})`)
    if (fd.diff?.text) lines.push(headTail(String(fd.diff.text), limit).text)
    if (fd.diff?.artifact_id) lines.push(`[full diff: npeer artifact-read ${fd.diff.artifact_id}]`)
  }
  if (d.output?.truncated || output?.truncated) lines.push(d.output?.artifact_id ? `[full output: npeer artifact-read ${d.output.artifact_id}]` : `[output truncated; ${d.output?.bytes ?? '?'} bytes in the worker's native history]`)
  return { text: lines.join('\n'), status }
}

export type Summary = {
  provider: string; modelRequested?: string; modelObserved?: string; effort?: string; profile?: string
  workerId: string; groupId?: string; taskId?: string; nativeId?: string; elapsedMs?: number
  resultArtifact?: string; patchArtifact?: string; changes?: { files?: number; added?: number; removed?: number; worktree?: string }
}

// The final answer the parent reads (spec 7.5.1). People read it too (the desktop app shows a handed-back report as
// the run's last message), so it closes with a line in words and one handle, the group, for npeer result.
// `shown`: the step already streamed the worker's final text, so only the closing lines follow it.
export function finalAnswer(terminal: Activity, s: Summary, owner: 'mirror' | 'native', generations: number[], shown = false,
                            folded: { generation: number; text: string }[] = [], mode?: string): string {
  const d = terminal.data ?? {}
  const status = String(d.status ?? 'completed')
  const body = headTail(String(d.final_text ?? ''), 12288)
  const handle = s.groupId ?? s.workerId
  const lines: string[] = []
  // Earlier generations of this run were shown as they ended; the notification carries only this message, so it repeats them.
  for (const f of folded) lines.push(`[generation ${f.generation}]`, f.text.trim() || '(no final text)', '')
  if (folded.length) lines.push(`[generation ${terminal.generation}]`)
  if (body.text.trim() && (!shown || folded.length)) lines.push(body.truncated ? body.text + `\n[… truncated; the full text: npeer result ${handle}]` : body.text)
  if (status === 'interrupted') lines.push('Interrupted: the text above is a partial result.')
  lines.push('', closingLine(terminal, s))
  if (d.error?.code) lines.push(`Error: ${d.error.code} ${String(d.error.message ?? '').slice(0, 300)}`.trim())
  if (s.changes?.files) lines.push(`Changed ${s.changes.files} file${s.changes.files === 1 ? '' : 's'} (+${s.changes.added ?? 0} −${s.changes.removed ?? 0})${s.changes.worktree ? ' in ' + s.changes.worktree : ''}`)
  if (generations.length > 1) lines.push(`Generations: ${generations.join(', ')}`)
  // A wait-mode route is never pushed: its result stays with NativePeers until asked for.
  lines.push(owner === 'native' && mode === 'wait' ? `Kept in NativePeers: npeer result ${handle}` : owner === 'native' ? `Delivered by NativePeers · npeer result ${handle}` : `Details: npeer result ${handle}`)
  return lines.join('\n')
}

// '— NativePeers · Codex · GPT-6.1 Sol · low effort · read-only · completed in 5s'
export function closingLine(terminal: Activity, s: Summary): string {
  const status = String(terminal.data?.status ?? 'completed')
  const provider = NAMES[s.provider as NpProvider] ?? s.provider
  const id = s.modelObserved ?? s.modelRequested
  const asked = s.modelObserved && s.modelRequested && s.modelObserved !== s.modelRequested ? ` (asked for ${modelLabel(s.modelRequested, s.provider as NpProvider)})` : ''
  const profile = s.profile === 'analysis' ? 'read-only' : s.profile === 'workspace' ? 'can edit' : s.profile
  return `— NativePeers · ${provider}${id ? ' · ' + modelLabel(id, s.provider as NpProvider) + asked : ''}${s.effort ? ' · ' + s.effort + ' effort' : ''}${profile ? ' · ' + profile : ''} · ${status.replace(/_/g, ' ')}${s.elapsedMs !== undefined ? ' in ' + duration(s.elapsedMs) : ''}`
}
