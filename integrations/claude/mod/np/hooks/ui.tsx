// The Lineage band, pane, and the transcript renderers for shadows. Trees use
// only Box, Text, Button, Code and Markdown, so they draw on every surface.
import type { EngineInterface } from 'claude-code'
import type { NpShadow, NpWorker } from '../types'
import { TAGS, modelLabel } from './agents'
import { request } from './bridge'
import { duration } from './mirror'
import { loadShadow, shadows } from './shadow'
import { getBridge, getFaults, getIndex, getLineage, getUi, setUi } from './state'

export const PANE_ID = 'np-lineage'
const ACTIVE = ['queued', 'starting', 'running', 'waiting']

function counts(workers: readonly NpWorker[]) {
  return { total: workers.length, running: workers.filter(w => ACTIVE.includes(w.status)).length,
    done: workers.filter(w => w.status === 'completed').length, failed: workers.filter(w => ['failed', 'timed_out'].includes(w.status)).length,
    stopped: workers.filter(w => ['canceled', 'interrupted'].includes(w.status)).length }
}

function elapsed(w: NpWorker, now: number): string {
  return w.startedAt ? duration((w.endedAt ?? now) - w.startedAt) : ''
}

// Claude Code drops a finished agent's task about 30 s after it ends; its view is refused from then on.
export async function openView($: EngineInterface, agentId: string, quiet = false): Promise<string | undefined> {
  const r: any = await $.ui.view({ agentId })
  if (r?.deny && !quiet) $.ui.toast('np: ' + r.deny)
  return r?.deny
}

// Esc in a worker's view stops that worker (gate G4), so the band offers a way back.
export async function mainView($: EngineInterface): Promise<void> {
  const r: any = await $.ui.view({})
  if (r?.deny) $.ui.toast('np: ' + r.deny)
}

export async function openPane($: EngineInterface): Promise<void> {
  await $.ui.open({ id: PANE_ID, title: 'NativePeers lineage', focus: true })
  await setUi($, { paneOpen: true })
}

export async function stopAll($: EngineInterface): Promise<number> {
  const groups = new Set((await getLineage($)).filter(w => ACTIVE.includes(w.status) && !w.parentWorkerId).map(w => w.groupId))
  for (const group of groups) {
    try { await request($, 'POST', `/v1/groups/${group}/cancel`, { client_request_id: `np-stop-all:${group}:${await $.clock.now()}`, reason: 'Stop all from Claude Code' }) }
    catch (error) { $.ui.toast('np: stop failed for ' + group + ': ' + String(error).slice(0, 120)) }
  }
  if (groups.size) $.ui.toast(`np: stopping ${groups.size} group${groups.size > 1 ? 's' : ''} and their descendants`)
  return groups.size
}

export async function interruptWorker($: EngineInterface, s: NpShadow): Promise<void> {
  try {
    const r = await request($, 'POST', `/v1/workers/${s.workerId}/interrupt`, { client_request_id: `np-int:${s.agentId}:${s.generation}:${await $.clock.now()}` })
    $.ui.toast(`np: interrupt ${r.state ?? 'requested'} (${TAGS[s.provider]})`)
  } catch (error) { $.ui.toast('np: interrupt failed: ' + String(error).slice(0, 160)) }
}

export async function stopWorker($: EngineInterface, s: NpShadow): Promise<void> {
  try {
    await request($, 'POST', `/v1/workers/${s.workerId}/cancel`, { client_request_id: `np-stop:${s.agentId}:${s.generation}`, reason: 'Stopped from the np band' })
    $.ui.toast(`np: stopping ${TAGS[s.provider]} · ${s.title}`)
  } catch (error) { $.ui.toast('np: stop failed: ' + String(error).slice(0, 160)) }
}

export async function copyResume($: EngineInterface, s: NpShadow, surface: string): Promise<void> {
  try {
    const r = await request($, 'GET', `/v1/workers/${s.workerId}/resume-command`)
    const text = String(r.command ?? r.shell ?? JSON.stringify(r)).slice(0, 4000)
    const copied: any = await $.ui.copy({ text, surface: surface as any })
    $.ui.toast(copied?.isCopied ? 'np: resume command copied' : 'np: resume command — ' + text.slice(0, 300))
  } catch (error) { $.ui.toast('np: no resume command: ' + String(error).slice(0, 160)) }
}

export async function renderBand($: EngineInterface, e: any, next: (e: any) => any): Promise<any> {
  const bridge = await getBridge($), workers = await getLineage($), index = await getIndex($), faults = await getFaults($)
  const ui = await getUi($)
  const viewed = e.props.view?.agentId as string | undefined
  if (e.props.hasSurvey || ui?.bandCollapsed) return next(e)
  if (bridge.state === 'off' && !workers.length) return next(e)
  const { Box, Text, Button } = $.ui.resolve(e)
  const now = await $.clock.now()
  if (viewed && index.includes(viewed)) {
    const s = await loadShadow($, viewed)
    if (s) {
      const w = workers.find(x => x.workerId === s.workerId)
      return (
        <Box flexDirection="row" flexWrap="wrap">
          <Text dimColor>↳ Subagent of main · {s.provider} · {w?.modelObserved ?? s.model ?? 'default'}{s.effort ? ' · ' + s.effort : ''}{s.profile ? ' · ' + s.profile : ''} · {duration(now - s.startedAt)}{w ? ' · ' + w.status : ''} </Text>
          {s.workerId ? <Button key="interrupt" label="Interrupt" onPress={() => interruptWorker($, s)} /> : null}
          {s.workerId ? <Button key="stop" label="Stop" onPress={() => stopWorker($, s)} /> : null}
          {s.workerId ? <Button key="resume" label="Resume cmd" onPress={(p: { surface: string }) => copyResume($, s, p.surface)} /> : null}
          <Button key="pane" label="Pane" onPress={() => openPane($)} />
          <Button key="main" label="Main" hotkey="m" onPress={() => mainView($)} />
        </Box>
      )
    }
  }
  const c = counts(workers.filter(w => !w.parentWorkerId))
  const head = `⟐ np · ${c.total} delegate${c.total === 1 ? '' : 's'} · ${c.running} running · ${c.done} done${c.stopped ? ` · ${c.stopped} stopped` : ''}${c.failed ? ` · ${c.failed} failed` : ''}`
  const status = faults.fallthrough ? ' · agent types off (fallthrough; /np doctor)' : bridge.state !== 'ready' ? ` · bridge ${bridge.state}${bridge.reason ? ': ' + bridge.reason.slice(0, 60) : ''}` : ''
  const rows = workers.filter(w => !w.parentWorkerId).slice(-4)
  const offers = workers.filter(w => ACTIVE.includes(w.status) && !w.agentId && w.mirrorOwner !== 'other').slice(0, 2)
  return (
    <Box flexDirection="column">
      <Box flexDirection="row">
        <Text dimColor>{head}{status} </Text>
        {c.running ? <Button key="stopall" label="Stop all" onPress={() => stopAll($)} /> : null}
        <Button key="pane" label="Pane" onPress={() => openPane($)} />
      </Box>
      {rows.map((w, n) => (
        <Box key={'row-' + w.workerId} flexDirection="row">
          <Text color={ACTIVE.includes(w.status) ? 'suggestion' : w.status === 'completed' ? 'success' : 'error'}>{TAGS[w.provider] ?? w.provider} </Text>
          <Text>{modelLabel(w.modelObserved ?? w.modelRequested, w.provider)}{w.effort ? ' ' + w.effort : ''} · {w.title} · {elapsed(w, now)} · {w.status} </Text>
          {w.agentId && index.includes(w.agentId) ? <Button key={'open-' + w.workerId} label="Open" hotkey={String(n + 1)} onPress={() => openView($, w.agentId!)} /> : null}
        </Box>
      ))}
      {offers.map(w => (
        <Box key={'offer-' + w.workerId} flexDirection="row">
          <Text dimColor>unmirrored: {TAGS[w.provider]} {w.title} </Text>
          <Button key={'attach:' + w.workerId} label="Attach" onPress={() => undefined} />
        </Box>
      ))}
    </Box>
  )
}

export async function renderPane($: EngineInterface, e: any, next: (e: any) => any): Promise<any> {
  const workers = await getLineage($), index = await getIndex($)
  const ui = await getUi($)
  const { Box, Text, Button } = $.ui.resolve(e)
  const now = await $.clock.now()
  const selected = workers.find(w => w.workerId === ui?.selected) ?? workers[workers.length - 1]
  const depth = (w: NpWorker): number => { let d = 0, cur: NpWorker | undefined = w;while (cur?.parentWorkerId && d < 6) { cur = workers.find(x => x.workerId === cur!.parentWorkerId);d++ }return d }
  return (
    <Box flexDirection="column">
      <Text bold>NativePeers lineage</Text>
      {workers.length ? null : <Text dimColor>No workers for this session yet.</Text>}
      {workers.map(w => (
        <Box key={'tree-' + w.workerId} flexDirection="row">
          <Button key={'sel-' + w.workerId} plain label={`${'  '.repeat(depth(w))}${TAGS[w.provider] ?? w.provider} ${w.title} · ${w.status} · ${elapsed(w, now)}`}
            onPress={() => setUi($, { paneOpen: true, selected: w.workerId })} />
        </Box>
      ))}
      {selected ? (
        <Box flexDirection="column">
          <Text dimColor>worker {selected.workerId} · group {selected.groupId}{selected.taskId ? ' · task ' + selected.taskId : ''} · generation {selected.generation}</Text>
          <Text dimColor>native {selected.nativeId ?? '?'} · requested {selected.modelRequested ?? 'default'} · observed {selected.modelObserved ?? '?'}{selected.effort ? ' · ' + selected.effort : ''}{selected.profile ? ' · ' + selected.profile : ''}</Text>
          <Text dimColor>{selected.outputTokens ? `${selected.outputTokens} output tokens · ` : ''}{selected.latest ?? ''}{selected.mirrorOwner === 'other' ? ' · mirrored in another session' : ''}</Text>
          <Box flexDirection="row">
            {selected.agentId && index.includes(selected.agentId) ? <Button key="p-open" label="Open" onPress={() => openView($, selected.agentId!)} /> : null}
            {!selected.agentId && ACTIVE.includes(selected.status) ? <Button key={'attach:' + selected.workerId} label="Attach" onPress={() => undefined} /> : null}
          </Box>
        </Box>
      ) : null}
    </Box>
  )
}

export async function renderToolUse($: EngineInterface, e: any, next: (e: any) => any): Promise<any> {
  const input = e.props.input as { kind?: string; title?: string; detail?: string } | undefined
  if (!input || typeof input !== 'object' || e.props.isExpanded) return next(e)
  const { Box, Text } = $.ui.resolve(e)
  return (
    <Box flexDirection="row">
      <Text bold color={e.props.isErrored ? 'error' : undefined}>{input.kind ?? 'tool'}</Text>
      <Text>  {String(input.title ?? '').slice(0, 200)}</Text>
      {e.props.isRunning ? <Text dimColor>  running…</Text> : null}
    </Box>
  )
}

export async function renderUserMessage($: EngineInterface, e: any, next: (e: any) => any): Promise<any> {
  const text = String(e.props.text ?? '')
  if (e.props.isExpanded || e.props.origin?.kind !== 'coordinator') return next(e)
  const adoption = text.startsWith('NativePeers worker "')
  if (!adoption && text.split('\n').length <= 3) return next(e)
  const { Box, Text } = $.ui.resolve(e)
  return (
    <Box flexDirection="row">
      <Text dimColor>{adoption ? 'Task from main · via NativePeers' : 'From main agent'} · </Text>
      <Text>{text.split('\n').find(l => l.trim())?.slice(0, 160) ?? ''}</Text>
      <Text dimColor> … (ctrl+o for the full brief)</Text>
    </Box>
  )
}

export async function renderSpinner($: EngineInterface, e: any, next: (e: any) => any): Promise<any> {
  const s = await loadShadow($, e.requestId)
  if (!s || !s.latest) return next(e)
  return next({ ...e, props: { ...e.props, message: `${TAGS[s.provider]}: ${s.latest}`.slice(0, 120) } })
}

export async function statusLine($: EngineInterface): Promise<void> {
  const workers = await getLineage($)
  const c = counts(workers.filter(w => !w.parentWorkerId))
  $.ui.status(c.running ? `◉${c.running} running · ✓${c.done} · ✗${c.failed}` : undefined)
}

export async function statusText($: EngineInterface): Promise<string> {
  const bridge = await getBridge($), workers = await getLineage($), list = await shadows($), faults = await getFaults($)
  const now = await $.clock.now()
  const lines = [`bridge: ${bridge.state}${bridge.reason ? ' — ' + bridge.reason : ''}${bridge.peer ? ' · peer ' + bridge.peer : ''}`,
    `fallthrough: ${faults.fallthrough ? 'DETECTED — ' + (faults.reason ?? '') : 'none'}`, `shadows: ${list.length}`, '']
  workers.forEach((w, n) => lines.push(`${n + 1}. ${TAGS[w.provider] ?? w.provider} ${modelLabel(w.modelObserved ?? w.modelRequested, w.provider)}${w.effort ? ' ' + w.effort : ''} · ${w.title} · ${w.status} · ${elapsed(w, now)} · worker ${w.workerId}${w.agentId ? '' : ' · unmirrored'}`))
  if (!workers.length) lines.push('No NativePeers workers for this session.')
  return lines.join('\n')
}
