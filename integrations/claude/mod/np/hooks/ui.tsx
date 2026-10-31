// The Lineage band, pane, and the transcript renderers for shadows. Trees use
// only Box, Text, Button, Code and Markdown, so they draw on every surface, and the
// pane's Input where the surface has one.
import type { EngineInterface } from 'claude-code'
import type { NpRow, NpShadow, NpUi, NpWorker } from '../types'
import { ACTIVE, NAMES, TAGS, counts, modelLabel, statusLabel, statusSummary } from './agents'
import { request, sleep } from './bridge'
import { duration } from './mirror'
import { loadShadow, newShadow } from './shadow'
import { getBridge, getFaults, getIndex, getLineage, getMember, getTranscript, getUi, setTranscript, setUi, startTranscript } from './state'
import { applyActivity } from './transcript'
import type { NpBridge, NpFaults } from '../types'

export const PANE_ID = 'np-lineage'
const PANE_TITLE = 'NativePeers'

// An error as a sentence fragment: its message, without the 'Error: ' a bare String() adds.
function reason(error: unknown): string {
  return (error instanceof Error ? error.message : String(error)).slice(0, 200)
}

// '2 delegates · 1 running · 1 done', with the bridge's state when it is not ready.
export function summary(c: ReturnType<typeof counts>, bridge: NpBridge, faults: NpFaults): string {
  const head = `${c.total} delegate${c.total === 1 ? '' : 's'} · ${c.running} running · ${c.done} done${c.stopped ? ` · ${c.stopped} stopped` : ''}${c.failed ? ` · ${c.failed} failed` : ''}`
  // The reason itself is /np doctor's: a raw error does not fit beside the buttons.
  return head + (faults.fallthrough ? ' · agent types off — /np doctor' : bridge.state === 'starting' ? ' · bridge starting'
    : bridge.state !== 'ready' ? ` · bridge ${bridge.state} — /np doctor` : '')
}

// The selected worker in words; its ids are one press away (Copy IDs), not on screen.
export function details(w: NpWorker): string[] {
  const asked = w.modelRequested && w.modelObserved && w.modelObserved !== w.modelRequested ? ` (asked for ${modelLabel(w.modelRequested, w.provider)})` : ''
  const access = w.profile === 'analysis' ? 'Read-only' : w.profile === 'workspace' ? 'Can edit its own workspace' : w.profile
  const id = w.modelObserved ?? w.modelRequested
  return [`Model: ${id ? modelLabel(id, w.provider) : 'the provider\'s default'}${w.effort ? `, ${w.effort} effort` : ''}${asked}`,
    access ? `Access: ${access}` : '', w.outputTokens ? `Output: ${w.outputTokens} tokens` : '', w.latest ? `Latest: ${w.latest}` : '',
    w.mirrorOwner === 'other' ? 'Shown in another Claude session.' : ''].filter(Boolean)
}

function elapsed(w: NpWorker, now: number): string {
  return w.startedAt ? duration((w.endedAt ?? now) - w.startedAt) : ''
}

// Claude Code drops a finished agent's task about 30 s after it ends; its view is refused from then on.
export async function openView($: EngineInterface, agentId: string, quiet = false): Promise<string | undefined> {
  const r: any = await $.ui.view({ agentId })
  if (r?.deny && !quiet) $.ui.toast(`Can't open that transcript here: ${r.deny}.`)
  return r?.deny
}

// Esc in a worker's view stops that worker (gate G4), so the band offers a way back.
export async function mainView($: EngineInterface): Promise<void> {
  const r: any = await $.ui.view({})
  if (r?.deny) $.ui.toast(`Can't go back to the main conversation: ${r.deny}.`)
}

export async function openPane($: EngineInterface): Promise<void> {
  await $.ui.open({ id: PANE_ID, title: PANE_TITLE, focus: true })
  await setUi($, { paneOpen: true })
}

// The desktop app's place for the band's content: the pane beside the conversation, opened (unfocused) when a
// delegate starts and left alone while it is open.
export async function paneBeside($: EngineInterface): Promise<void> {
  if ((await $.ui.panes()).some(p => p.id === PANE_ID)) return
  await $.ui.open({ id: PANE_ID, title: PANE_TITLE })
}

// What the pane's Interrupt, Stop and Resume cmd act on: the lineage row names the worker even while its shadow here
// is still binding (no workerId yet).
export function controlTarget(w: NpWorker, held: NpShadow | undefined): NpShadow {
  return { ...(held ?? newShadow('', w.provider, w.title, '', 0, { generation: w.generation })), workerId: w.workerId }
}

export async function copyIds($: EngineInterface, w: NpWorker, surface: string): Promise<void> {
  const text = [`worker ${w.workerId}`, `group ${w.groupId}`, w.taskId && `task ${w.taskId}`, w.nativeId && `native ${w.nativeId}`, `generation ${w.generation}`].filter(Boolean).join('\n')
  const copied: any = await $.ui.copy({ text, surface: surface as any })
  $.ui.toast(copied?.isCopied ? 'Worker IDs copied.' : text)
}

export async function stopAll($: EngineInterface): Promise<number> {
  const groups = new Set((await getLineage($)).filter(w => ACTIVE.includes(w.status) && !w.parentWorkerId).map(w => w.groupId))
  for (const group of groups) {
    try { await request($, 'POST', `/v1/groups/${group}/cancel`, { client_request_id: `np-stop-all:${group}:${await $.clock.now()}`, reason: 'Stop all from Claude Code' }) }
    catch (error) { $.ui.toast(`Stop failed for group ${group}: ${reason(error)}`) }
  }
  if (groups.size) $.ui.toast(`Stopping ${groups.size} group${groups.size > 1 ? 's' : ''} and their descendants.`)
  return groups.size
}

export async function interruptWorker($: EngineInterface, s: NpShadow): Promise<void> {
  try {
    const r = await request($, 'POST', `/v1/workers/${s.workerId}/interrupt`, { client_request_id: `np-int:${s.agentId}:${s.generation}:${await $.clock.now()}` })
    $.ui.toast(`Interrupt ${r.state ?? 'requested'}: ${NAMES[s.provider]} · ${s.title}.`)
  } catch (error) { $.ui.toast(`Interrupt failed: ${reason(error)}`) }
}

export async function stopWorker($: EngineInterface, s: NpShadow): Promise<void> {
  try {
    await request($, 'POST', `/v1/workers/${s.workerId}/cancel`, { client_request_id: `np-stop:${s.agentId}:${s.generation}`, reason: 'Stopped from the np band' })
    $.ui.toast(`Stopping ${NAMES[s.provider]} · ${s.title}.`)
  } catch (error) { $.ui.toast(`Stop failed: ${reason(error)}`) }
}

export async function copyResume($: EngineInterface, s: NpShadow, surface: string): Promise<void> {
  try {
    const r = await request($, 'GET', `/v1/workers/${s.workerId}/resume-command`)
    const text = String(r.command ?? r.shell ?? JSON.stringify(r)).slice(0, 4000)
    const copied: any = await $.ui.copy({ text, surface: surface as any })
    $.ui.toast(copied?.isCopied ? 'Resume command copied.' : `Resume command: ${text.slice(0, 300)}`)
  } catch (error) {
    // A worker still open for follow-ups holds its native conversation: it resumes from a shell only once released.
    $.ui.toast(/has not released its native writer/.test(reason(error)) ? 'This worker is still open for follow-ups: message it from the NativePeers pane instead.'
      : `No resume command: ${reason(error)}`)
  }
}

const following = new Set<string>()

// Shows a worker's conversation in the pane and keeps it current while it is shown.
export async function openConversation($: EngineInterface, workerId: string, focus = false): Promise<void> {
  await setUi($, { paneOpen: true, viewing: workerId })
  if (focus) await $.ui.open({ id: PANE_ID, title: PANE_TITLE, focus: true })
  void followConversation($, workerId)
}

// Long-polls the worker's activity while its conversation is on screen; five failed reads in a row end it until the
// person opens it or sends to it again.
export async function followConversation($: EngineInterface, workerId: string): Promise<void> {
  if (following.has(workerId)) return
  following.add(workerId)
  try {
    let t = await startTranscript($, workerId), misses = 0
    while ((await getUi($)).viewing === workerId && misses < 5) {
      try {
        const batch = await request($, 'GET', `/v1/workers/${workerId}/activity?after=${t.cursor}&wait_ms=20000&max=256`)
        misses = 0
        const events = Array.isArray(batch.events) ? batch.events : []
        if (events.length) { t = applyActivity(t, events);await setTranscript($, t) }
      } catch {
        misses += 1;await sleep($, 2000)
      }
    }
  } finally {
    following.delete(workerId)
  }
}

// A message typed in the pane goes to the worker as a person's follow-up: a live worker takes it after its current
// turn, a released one resumes with it. Its answer shows in this conversation.
export async function sendToWorker($: EngineInterface, w: NpWorker, text: string): Promise<void> {
  if (!text.trim()) return
  // A new key draws an empty field.
  await setUi($, { sent: ((await getUi($)).sent ?? 0) + 1 })
  const name = NAMES[w.provider] ?? w.provider
  try {
    const r = await request($, 'POST', `/v1/workers/${w.workerId}/input`, { client_request_id: `np-pane:${w.workerId}:${await $.clock.now()}`, text, origin: 'human-claude-view', mode: 'queue' })
    $.ui.toast(r.disposition === 'refused' ? `Not delivered: ${r.reason ?? 'refused'}.` : r.disposition === 'resumed' ? `Sent. ${name} is picking the conversation back up.` : `Sent to ${name}.`)
    void followConversation($, w.workerId)
  } catch (error) { $.ui.toast(`Not delivered: ${reason(error)}.`) }
}

// The desktop app draws no band (band: auto): its pane beside the conversation carries the same summary.
export async function renderBand($: EngineInterface, e: any, next: (e: any) => any, always = false): Promise<any> {
  if (e.surface === 'desktop' && !always) return next(e)
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
  const rows = workers.filter(w => !w.parentWorkerId).slice(-4)
  // A running worker with no live shadow here (none, or one that faulted or ended) is offered for attach.
  const offers: NpWorker[] = []
  for (const w of workers) {
    if (offers.length >= 2 || !ACTIVE.includes(w.status) || w.mirrorOwner === 'other') continue
    if (!w.agentId || ['FAULTED', 'DETACHED', 'COMPLETED'].includes((await getMember($, w.agentId)).phase)) offers.push(w)
  }
  return (
    <Box flexDirection="column">
      <Box flexDirection="row" columnGap="sm">
        <Box flexShrink={1}><Text dimColor wrap="truncate-end">⟐ np · {summary(c, bridge, faults)}</Text></Box>
        {c.running ? <Button key="stopall" label="Stop all" onPress={() => stopAll($)} /> : null}
        <Button key="pane" label="Pane" onPress={() => openPane($)} />
      </Box>
      {rows.map((w, n) => (
        <Box key={'row-' + w.workerId} flexDirection="row" columnGap="sm">
          <Text color={ACTIVE.includes(w.status) ? 'suggestion' : w.status === 'completed' ? 'success' : 'error'}>{TAGS[w.provider] ?? w.provider}</Text>
          <Box flexShrink={1}><Text wrap="truncate-end">{modelLabel(w.modelObserved ?? w.modelRequested, w.provider)}{w.effort ? ' ' + w.effort : ''} · {w.title} · {elapsed(w, now)} · {w.status}</Text></Box>
          {w.agentId && index.includes(w.agentId) && e.surface !== 'desktop' ? <Button key={'open-' + w.workerId} label="Open" hotkey={String(n + 1)} onPress={() => openView($, w.agentId!)} /> : null}
        </Box>
      ))}
      {offers.map(w => (
        <Box key={'offer-' + w.workerId} flexDirection="row" columnGap="sm">
          <Box flexShrink={1}><Text dimColor wrap="truncate-end">unmirrored: {TAGS[w.provider]} {w.title}</Text></Box>
          <Button key={'attach:' + w.workerId} label="Attach" onPress={() => undefined} />
        </Box>
      ))}
    </Box>
  )
}

// The pane lists this session's workers; pressing one shows its conversation. On the desktop app (band: auto) it
// also carries the band's summary.
export async function renderPane($: EngineInterface, e: any, next: (e: any) => any, carriesBand = false): Promise<any> {
  const workers = await getLineage($), ui = await getUi($)
  const viewing = ui?.viewing ? workers.find(w => w.workerId === ui.viewing) : undefined
  if (viewing) return renderConversation($, e, viewing, ui)
  const bridge = await getBridge($), faults = await getFaults($)
  const { Box, Text, Button } = $.ui.resolve(e)
  const now = await $.clock.now()
  const c = counts(workers.filter(w => !w.parentWorkerId))
  const band = e.surface === 'desktop' && carriesBand && workers.length > 0
  const depth = (w: NpWorker): number => { let d = 0, cur: NpWorker | undefined = w;while (cur?.parentWorkerId && d < 6) { cur = workers.find(x => x.workerId === cur!.parentWorkerId);d++ }return d }
  return (
    <Box flexDirection="column">
      {band ? <Text dimColor>{summary(c, bridge, faults)}</Text> : null}
      {band && c.running ? <Box flexDirection="row"><Button key="stopall" label="Stop all" onPress={() => stopAll($)} /></Box> : null}
      {workers.length ? null : <Text dimColor>No workers in this session yet.</Text>}
      {workers.map(w => (
        <Box key={'tree-' + w.workerId} flexDirection="column">
          <Button key={'sel-' + w.workerId} plain label={'  '.repeat(depth(w)) + w.title} onPress={() => openConversation($, w.workerId)} />
          <Text dimColor>{'  '.repeat(depth(w))}{NAMES[w.provider] ?? w.provider} · {statusLabel(w.status)}{w.startedAt ? ' · ' + elapsed(w, now) : ''}</Text>
        </Box>
      ))}
      {workers.length ? <Text dimColor>Press a worker to read its conversation and message it.</Text> : null}
    </Box>
  )
}

// One row of a conversation. The provider's name heads a run of its messages.
function conversationRow(R: any, r: NpRow, n: number, previous: NpRow | undefined, name: string): any {
  const { Box, Text, Code, Markdown } = R
  const body = (text: string) => Markdown ? <Markdown text={text} /> : <Text>{text}</Text>
  if (r.kind === 'task' || r.kind === 'you') return (
    <Box key={'row-' + n} flexDirection="column"><Text bold>{r.kind === 'you' ? 'You' : 'Task from main'}</Text>{body(r.text)}</Box>)
  if (r.kind === 'text') return (
    <Box key={'row-' + n} flexDirection="column">{previous?.kind === 'text' ? null : <Text bold>{name}</Text>}{body(r.text)}</Box>)
  if (r.kind === 'tool') return (
    <Box key={'row-' + n} flexDirection="column"><Text dimColor>▸ {r.text}{r.status ? ' · ' + r.status : ''}</Text>{r.detail && Code ? <Code source={r.detail} /> : null}</Box>)
  return <Text key={'row-' + n} dimColor>{r.text}</Text>
}

// A worker's conversation in the pane: what it was asked, what it said and did, and a field to message it.
async function renderConversation($: EngineInterface, e: any, w: NpWorker, ui: NpUi): Promise<any> {
  const R: any = $.ui.resolve(e)
  const { Box, Text, Button, Input } = R
  const t = await getTranscript($), now = await $.clock.now()
  const rows = t.workerId === w.workerId ? t.rows : []
  const running = ACTIVE.includes(w.status), name = NAMES[w.provider] ?? w.provider
  // As in the band: a running worker with no live shadow here (none, or one that faulted or ended) can be attached.
  const attachable = running && w.mirrorOwner !== 'other' && (!w.agentId || ['FAULTED', 'DETACHED', 'COMPLETED'].includes((await getMember($, w.agentId)).phase))
  const target = controlTarget(w, w.agentId ? await loadShadow($, w.agentId) : undefined)
  return (
    <Box flexDirection="column">
      <Box flexDirection="row"><Button key="back" label="‹ Workers" onPress={() => setUi($, { viewing: undefined })} /></Box>
      <Text bold>{w.title}</Text>
      <Text dimColor>{name} · {statusLabel(w.status)}{w.startedAt ? ' · ' + elapsed(w, now) : ''}</Text>
      {details(w).map((line, n) => <Text key={'detail-' + n} dimColor>{line}</Text>)}
      {rows.length ? null : <Text dimColor>{t.workerId === w.workerId ? 'No activity yet.' : 'Loading the conversation…'}</Text>}
      {rows.map((r, n) => conversationRow(R, r, n, rows[n - 1], name))}
      {Input ? <Input key={'say-' + (ui.sent ?? 0)} placeholder={`Message ${name}…`} submitLabel="send" autoFocus onSubmit={(value: string) => sendToWorker($, w, value)} /> : null}
      <Box flexDirection="row" flexWrap="wrap" columnGap="sm">
        {running ? <Button key="p-interrupt" label="Interrupt" onPress={() => interruptWorker($, target)} /> : null}
        {running ? <Button key="p-stop" label="Stop" onPress={() => stopWorker($, target)} /> : null}
        {attachable ? <Button key={'attach:' + w.workerId} label="Attach" onPress={() => undefined} /> : null}
        <Button key="p-ids" label="Copy IDs" onPress={(p: { surface: string }) => copyIds($, w, p.surface)} />
      </Box>
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
  $.ui.status(statusSummary(await getLineage($)))
}

export async function statusText($: EngineInterface): Promise<string> {
  const bridge = await getBridge($), workers = await getLineage($), faults = await getFaults($)
  const now = await $.clock.now()
  const lines = [`Bridge: ${bridge.state === 'ready' ? 'Connected' : statusLabel(bridge.state)}${bridge.reason ? ` (${bridge.reason})` : ''}`]
  if (faults.fallthrough) lines.push(`Fallthrough detected: ${faults.reason ?? 'unknown'}. Run /np doctor.`)
  lines.push('')
  workers.forEach((w, n) => lines.push(`${n + 1}. ${NAMES[w.provider] ?? w.provider} ${modelLabel(w.modelObserved ?? w.modelRequested, w.provider)}${w.effort ? ' ' + w.effort : ''} · ${w.title} · ${statusLabel(w.status)}${w.startedAt ? ' · ' + elapsed(w, now) : ''}${w.agentId ? '' : ' · not mirrored here'}`))
  if (!workers.length) lines.push('No NativePeers workers in this session.')
  return lines.join('\n')
}

export type DoctorFacts = {
  version: string; minimum: string; supported: boolean; surface: string; npeer: string; connected: boolean; state: string; reason?: string; restarted?: string
  hello?: { protocol?: number; providers?: string[]; schema?: string }; helloError?: string; fallthrough?: { cleared: boolean; reason?: string }
}

// /np doctor in sentences; Claude Code puts "np: " before the first line. The desktop app keeps its own copy of
// Claude Code and updates it itself, so the doctor says where its version comes from.
export function doctorText(f: DoctorFacts): string {
  const head = !f.supported ? `Not ready: Claude Code ${f.version} is older than ${f.minimum}, the oldest version this mod supports.`
    : !f.connected ? `Not ready: the bridge is ${f.state}${f.reason ? ` (${f.reason})` : ''}.` : f.helloError ? 'Not ready: the bridge did not answer.' : 'Ready.'
  const schema = f.hello?.schema ? f.hello.schema.replace(/^nativepeers\.activity\./, '') : ''
  const lines = [head]
  if (f.restarted) lines.push(f.restarted)
  lines.push(`Claude Code: ${f.version}${f.surface === 'desktop' ? ' · the desktop app\'s own copy, which the app updates (Claude › Check for Updates…)' : ''}`)
  lines.push(`Bridge: ${f.connected ? 'Connected' : statusLabel(f.state)}${f.hello?.protocol !== undefined ? ` (protocol ${f.hello.protocol}${schema ? ', activity schema ' + schema : ''})` : ''}`)
  if (f.hello?.providers?.length) lines.push(`Providers: ${f.hello.providers.map(p => NAMES[p as keyof typeof NAMES] ?? p).join(', ')}`)
  if (f.helloError) lines.push(`Bridge error: ${f.helloError}`)
  if (f.fallthrough) lines.push(f.fallthrough.cleared ? `Fallthrough cleared (it was: ${f.fallthrough.reason ?? 'unknown'}). np agent types are offered again.`
    : `Fallthrough: ${f.fallthrough.reason ?? 'detected'}. np agent types stay off until the bridge is ready.`)
  lines.push(`npeer: ${f.npeer}`)
  return lines.join('\n')
}
