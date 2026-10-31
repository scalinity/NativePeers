// Every $.state value of the mod and the only reads and writes of them. The
// validator resolves a read or update target only to an atom of the same file.
import { atom, read, update } from 'claude-code'
import type { EngineInterface } from 'claude-code'
import type { NpBridge, NpFaults, NpShadow, NpTranscript, NpUi, NpWorker } from '../types'

const bridgeAtom = atom({ plugin: 'np', key: 'bridge' } as const, { state: 'off', since: 0 } as NpBridge)
const lineageAtom = atom({ plugin: 'np', key: 'lineage' } as const, [] as NpWorker[], { shape: 'np-lineage-1' })
const indexAtom = atom({ plugin: 'np', key: 'shadows' } as const, [] as string[])
const faultsAtom = atom({ plugin: 'np', key: 'faults' } as const, { fallthrough: false } as NpFaults)
const uiAtom = atom({ plugin: 'np', key: 'ui' } as const, { bandCollapsed: false, paneOpen: false } as NpUi)
const transcriptAtom = atom({ plugin: 'np', key: 'transcript' } as const, { workerId: '', cursor: 0, rows: [] } as NpTranscript, { shape: 'np-transcript-1' })
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

export async function getTranscript($: EngineInterface): Promise<NpTranscript> { return read($, transcriptAtom) }
// Only the conversation still shown is written: a follower of a worker no longer viewed leaves the state alone.
export async function setTranscript($: EngineInterface, value: NpTranscript): Promise<void> {
  await update($, transcriptAtom, current => current.workerId === value.workerId ? value : current)
}
// Showing another worker starts its conversation empty; showing the same one again keeps what was read.
export async function startTranscript($: EngineInterface, workerId: string): Promise<NpTranscript> {
  let out: NpTranscript = { workerId, cursor: 0, rows: [] }
  await update($, transcriptAtom, current => (out = current.workerId === workerId ? current : { workerId, cursor: 0, rows: [] }))
  return out
}

export async function getIndex($: EngineInterface): Promise<string[]> { return read($, indexAtom) }

export async function getMember($: EngineInterface, agentId: string): Promise<NpShadow> {
  return read($, atom({ ...family, id: agentId }, NONE, { shape: 'np-shadow-1' }))
}

export async function setMember($: EngineInterface, value: NpShadow): Promise<void> {
  await update($, atom({ ...family, id: value.agentId }, NONE, { shape: 'np-shadow-1' }), () => value)
  await update($, indexAtom, list => list.includes(value.agentId) ? list : [...list, value.agentId])
}
