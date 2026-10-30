// The np mod's state contract: what it keeps in $.state, by key.
export type NpProvider = 'codex' | 'claude' | 'antigravity' | 'grok'

export type NpBridge = {
  state: 'off' | 'starting' | 'ready' | 'degraded' | 'down'
  version?: string
  peer?: string
  reason?: string
  since: number
  steer?: Partial<Record<NpProvider, boolean>>
}

export type NpWorker = {
  workerId: string
  groupId: string
  taskId?: string
  generation: number
  provider: NpProvider
  modelRequested?: string
  modelObserved?: string
  effort?: string
  profile?: string
  title: string
  status: 'queued' | 'starting' | 'running' | 'waiting' | 'completed' | 'failed' | 'canceled' | 'interrupted' | 'timed_out'
  startedAt?: number
  endedAt?: number
  outputTokens?: number
  agentId?: string
  parentWorkerId?: string
  latest?: string
  mirrorOwner?: 'this' | 'other' | null
  nativeId?: string
  resumable?: boolean
}

export type NpPending = { item: string; kind: string; startSeq: number; title: string; detail: string; generation: number; immediate?: boolean }

export type NpInput = { uuid: string; text: string; origin: 'human-claude-view' | 'parent-model'; forwarded: boolean; disposition?: string; reason?: string; generation?: number; attempts: number }

export type NpShadow = {
  agentId: string
  workerId?: string
  groupId?: string
  taskId?: string
  generation: number
  provider: NpProvider
  model?: string
  effort?: string
  profile?: string
  title: string
  brief?: string
  spawnError?: string
  phase: 'SPAWNING' | 'BINDING' | 'STREAMING' | 'TOOL_PENDING' | 'COMPLETING' | 'COMPLETED' | 'FAULTED' | 'DETACHED'
  deliveryOwner: 'mirror' | 'native'
  cursor: number
  inflight?: { from: number; to: number }
  pendingRefs: Record<string, NpPending>
  inputs: NpInput[]
  briefConsumed: boolean
  surfaced?: { generation: number; taskId?: string }
  terminals: number[]
  fallthrough?: boolean
  lastFault?: string
  session: string
  startedAt: number
  latest?: string
  // The generation NativePeers delivers natively (an adopted worker's initial
  // task); every other generation this shadow carries is mirror-owned.
  nativeGeneration?: number
  usageCumulative?: number
  waits: number
}

export type NpFaults = { fallthrough: boolean; reason?: string; at?: number }

declare module 'claude-code' {
  interface PluginState {
    np: {
      bridge: NpBridge
      lineage: Shaped<NpWorker[]>
      shadow: StateFamily<Shaped<NpShadow>>
      shadows: string[]
      ui: { bandCollapsed: boolean; paneOpen: boolean; selected?: string }
      faults: NpFaults
    }
  }
}
