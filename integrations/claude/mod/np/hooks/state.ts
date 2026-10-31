// Every $.state value of the mod and the only reads and writes of them. The
// validator resolves a read or update target only to an atom of the same file.
import { atom, read, update } from 'claude-code'
import type { EngineInterface } from 'claude-code'
import type { NpBridge, NpDraft, NpFaults, NpShadow, NpTranscript, NpUi, NpWorker } from '../types'

const bridgeAtom = atom({ plugin: 'np', key: 'bridge' } as const, { state: 'off', since: 0 } as NpBridge)
const lineageAtom = atom({ plugin: 'np', key: 'lineage' } as const, [] as NpWorker[], { shape: 'np-lineage-1' })
const indexAtom = atom({ plugin: 'np', key: 'shadows' } as const, [] as string[])
const faultsAtom = atom({ plugin: 'np', key: 'faults' } as const, { fallthrough: false } as NpFaults)
const uiAtom = atom({ plugin: 'np', key: 'ui' } as const, { bandCollapsed: false, paneOpen: false } as NpUi)
const transcripts = { plugin: 'np', key: 'transcript' } as const
const NO_TRANSCRIPT: NpTranscript = { workerId: '', cursor: 0, rows: [] }
const family = { plugin: 'np', key: 'shadow' } as const

export const NONE: NpShadow = { agentId: '', generation: 0, provider: 'codex', title: '', phase: 'DETACHED', deliveryOwner: 'mirror', cursor: 0,
  pendingRefs: {}, inputs: [], briefConsumed: true, terminals: [], session: '', startedAt: 0, waits: 0 }

export async function getBridge($: EngineInterface): Promise<NpBridge> { return read($, bridgeAtom) }
export async function setBridge($: EngineInterface, value: NpBridge): Promise<void> { await update($, bridgeAtom, () => value) }
// Shared by every module instance of this session (a hot reload overlaps two), so each rule is judged inside
// the updater, which update() runs again on every retry.
// A failed start never replaces a ready state: another start, here or in the other module, connected.
export async function bridgeFailed($: EngineInterface, value: NpBridge): Promise<void> {
  await update($, bridgeAtom, current => current.state === 'ready' ? current : value)
}
// /np doctor's restore: only over a state that is not ready, and only while the restoring connection lives.
export async function bridgeRestored($: EngineInterface, value: NpBridge, live: () => boolean): Promise<void> {
  await update($, bridgeAtom, current => current.state !== 'ready' && live() ? value : current)
}
// A successor may reuse the socket path, so an exit is matched by its launch, not its socket.
export async function bridgeExited($: EngineInterface, launch: string, value: NpBridge): Promise<void> {
  await update($, bridgeAtom, current => current.launch === launch ? value : current)
}

export async function getLineage($: EngineInterface): Promise<NpWorker[]> { return read($, lineageAtom) }
// The bridge names a worker's shadow only while its lease lives; a lease that lapsed during a bridge outage keeps this
// session's own shadow link unless another session now mirrors the worker.
export async function setLineage($: EngineInterface, value: NpWorker[]): Promise<void> {
  await update($, lineageAtom, prev => value.map(w => w.agentId || w.mirrorOwner === 'other' ? w
    : { ...w, agentId: prev.find(p => p.workerId === w.workerId)?.agentId }))
}

export async function getFaults($: EngineInterface): Promise<NpFaults> { return read($, faultsAtom) }
export async function setFaults($: EngineInterface, value: NpFaults): Promise<void> { await update($, faultsAtom, () => value) }

export async function getUi($: EngineInterface) { return read($, uiAtom) }
export async function setUi($: EngineInterface, change: Partial<NpUi>): Promise<void> {
  await update($, uiAtom, ui => ({ ...ui, ...change }))
}

// Opens or closes one conversation row (a long message, a tool's output); the newest 200 choices are kept.
export async function toggleExpanded($: EngineInterface, key: string): Promise<void> {
  await update($, uiAtom, ui => {
    const held = ui.expanded ?? []
    return { ...ui, expanded: held.includes(key) ? held.filter(k => k !== key) : [...held, key].slice(-200) }
  })
}

// A worker's unsent message. A settling answer touches only the draft of its own request: a newer one stays.
export async function putDraft($: EngineInterface, draft: NpDraft, onlyIf?: string): Promise<void> {
  await update($, uiAtom, ui => onlyIf !== undefined && ui.drafts?.[draft.workerId]?.requestId !== onlyIf ? ui
    : { ...ui, drafts: { ...(ui.drafts ?? {}), [draft.workerId]: draft } })
}
export async function clearDraft($: EngineInterface, workerId: string, requestId: string): Promise<void> {
  await update($, uiAtom, ui => {
    if (ui.drafts?.[workerId]?.requestId !== requestId) return ui
    const { [workerId]: _sent, ...rest } = ui.drafts
    return { ...ui, drafts: rest }
  })
}
// Takes the worker's single message slot and draws an empty field, in one update; false while its last message is
// unresolved (sending or uncertain), so two quick sends never both go out.
export async function reserveDraft($: EngineInterface, draft: NpDraft): Promise<boolean> {
  let reserved = false
  await update($, uiAtom, ui => {
    const held = ui.drafts?.[draft.workerId]
    reserved = !(held && (held.state === 'sending' || held.state === 'uncertain'))
    return reserved ? { ...ui, sent: (ui.sent ?? 0) + 1, drafts: { ...(ui.drafts ?? {}), [draft.workerId]: draft } } : ui
  })
  return reserved
}
// A draft still 'sending' after a reload lost its answer: it may have arrived, so only a retry under its id is safe.
export async function settleInterruptedDrafts($: EngineInterface): Promise<void> {
  await update($, uiAtom, ui => {
    const held = Object.values(ui.drafts ?? {})
    if (!held.some(d => d.state === 'sending')) return ui
    const drafts: Record<string, NpDraft> = {}
    for (const d of held) drafts[d.workerId] = d.state === 'sending' ? { ...d, state: 'uncertain', reason: 'a reload interrupted it' } : d
    return { ...ui, drafts }
  })
}

// One conversation per worker: a follower writes only its own worker's, so showing another never races it.
export async function getTranscript($: EngineInterface, workerId: string): Promise<NpTranscript> {
  const t = await read($, atom({ ...transcripts, id: workerId }, NO_TRANSCRIPT, { shape: 'np-transcript-1' }))
  return t.workerId === workerId ? t : { workerId, cursor: 0, rows: [] }
}
export async function setTranscript($: EngineInterface, value: NpTranscript): Promise<void> {
  await update($, atom({ ...transcripts, id: value.workerId }, NO_TRANSCRIPT, { shape: 'np-transcript-1' }), () => value)
}

export async function getIndex($: EngineInterface): Promise<string[]> { return read($, indexAtom) }

export async function getMember($: EngineInterface, agentId: string): Promise<NpShadow> {
  return read($, atom({ ...family, id: agentId }, NONE, { shape: 'np-shadow-1' }))
}

export async function setMember($: EngineInterface, value: NpShadow): Promise<void> {
  await update($, atom({ ...family, id: value.agentId }, NONE, { shape: 'np-shadow-1' }), () => value)
  await update($, indexAtom, list => list.includes(value.agentId) ? list : [...list, value.agentId])
}
