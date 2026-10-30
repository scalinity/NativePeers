// Every $.state value of the mod and the only reads and writes of them. The
// validator resolves a read or update target only to an atom of the same file.
import { atom, read, update } from 'claude-code'
import type { EngineInterface } from 'claude-code'
import type { NpBridge, NpFaults, NpShadow, NpWorker } from '../types'

const bridgeAtom = atom({ plugin: 'np', key: 'bridge' } as const, { state: 'off', since: 0 } as NpBridge)
const lineageAtom = atom({ plugin: 'np', key: 'lineage' } as const, [] as NpWorker[], { shape: 'np-lineage-1' })
const indexAtom = atom({ plugin: 'np', key: 'shadows' } as const, [] as string[])
const faultsAtom = atom({ plugin: 'np', key: 'faults' } as const, { fallthrough: false } as NpFaults)
const uiAtom = atom({ plugin: 'np', key: 'ui' } as const, { bandCollapsed: false, paneOpen: false } as { bandCollapsed: boolean; paneOpen: boolean; selected?: string })
const family = { plugin: 'np', key: 'shadow' } as const

export const NONE: NpShadow = { agentId: '', generation: 0, provider: 'codex', title: '', phase: 'DETACHED', deliveryOwner: 'mirror', cursor: 0,
  pendingRefs: {}, inputs: [], briefConsumed: true, terminals: [], session: '', startedAt: 0, waits: 0 }

export async function getBridge($: EngineInterface): Promise<NpBridge> { return read($, bridgeAtom) }
export async function setBridge($: EngineInterface, value: NpBridge): Promise<void> { await update($, bridgeAtom, () => value) }
export async function bridgeExited($: EngineInterface, socket: string, value: NpBridge): Promise<void> {
  await update($, bridgeAtom, current => current.socket === socket ? value : current)
}

export async function getLineage($: EngineInterface): Promise<NpWorker[]> { return read($, lineageAtom) }
export async function setLineage($: EngineInterface, value: NpWorker[]): Promise<void> { await update($, lineageAtom, () => value) }

export async function getFaults($: EngineInterface): Promise<NpFaults> { return read($, faultsAtom) }
export async function setFaults($: EngineInterface, value: NpFaults): Promise<void> { await update($, faultsAtom, () => value) }

export async function getUi($: EngineInterface) { return read($, uiAtom) }
export async function setUi($: EngineInterface, change: { paneOpen?: boolean; selected?: string; bandCollapsed?: boolean }): Promise<void> {
  await update($, uiAtom, ui => ({ ...ui, ...change }))
}

export async function getIndex($: EngineInterface): Promise<string[]> { return read($, indexAtom) }

export async function getMember($: EngineInterface, agentId: string): Promise<NpShadow> {
  return read($, atom({ ...family, id: agentId }, NONE, { shape: 'np-shadow-1' }))
}

export async function setMember($: EngineInterface, value: NpShadow): Promise<void> {
  await update($, atom({ ...family, id: value.agentId }, NONE, { shape: 'np-shadow-1' }), () => value)
  await update($, indexAtom, list => list.includes(value.agentId) ? list : [...list, value.agentId])
}
