// The np mod's state contract: what it keeps in $.state, by key.
export type NpProvider = 'codex' | 'claude' | 'antigravity' | 'grok'

export type NpBridge = {
  state: 'off' | 'starting' | 'ready' | 'degraded' | 'down'
  version?: string
  peer?: string
  reason?: string
  since: number
  steer?: Partial<Record<NpProvider, boolean>>
  socket?: string
  // A nonsecret id unique to each bridge launch, across module instances: an exit or restore names its own launch.
  launch?: string
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
  // Generations a follow-up queued while the worker ran folded into this run: the run's one notification carries them.
  folded?: { generation: number; taskId?: string; text: string }[]
  surfacedFolded?: { generation: number; taskId?: string }[]
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
  // The engine requires this run to end with a SubagentHandback call (sessions in auto mode).
  handback?: boolean
  // The report the parent reads, kept until the run completes: an enforcement step hands it back again.
  report?: string
  // SubagentHandback calls made for the current report: a refused call is not repeated without end.
  handbacks?: number
}

export type NpFaults = { fallthrough: boolean; reason?: string; at?: number }

// The conversation the pane shows for one worker, built from its activity log.
// `full` is kept on the running generation's text rows only, to match its final answer.
export type NpRow = { kind: 'task' | 'you' | 'text' | 'tool' | 'note'; text: string; item?: string; status?: string; detail?: string; gen?: number; full?: string }
export type NpTranscript = { workerId: string; cursor: number; rows: NpRow[] }

// A message typed in the pane until the bridge answers: a refused one returns to the field, an uncertain one is retried
// with the same request id, which the bridge admits once.
export type NpDraft = { workerId: string; text: string; requestId: string; state: 'sending' | 'refused' | 'uncertain'; reason?: string }
export type NpUi = { bandCollapsed: boolean; paneOpen: boolean; selected?: string; viewing?: string; sent?: number; drafts?: Record<string, NpDraft> }

declare module 'claude-code' {
  interface PluginState {
    np: {
      bridge: NpBridge
      lineage: Shaped<NpWorker[]>
      shadow: StateFamily<Shaped<NpShadow>>
      shadows: string[]
      ui: NpUi
      transcript: Shaped<NpTranscript>
      faults: NpFaults
    }
  }
}
