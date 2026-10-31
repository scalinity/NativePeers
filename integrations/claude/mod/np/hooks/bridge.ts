// The session bridge: launch, authenticated requests, heartbeat and lineage.
// Every wait here is a $ call in flight (a $.http.fetch long-poll or a
// $.process.run sleep), never a plain promise: a plain await spends the hook's
// own 10 s budget (gate X11).
import type { EngineInterface } from 'claude-code'
import type { NpWorker } from '../types'
import { bridgeExited, bridgeFailed, bridgeRestored, setBridge, setLineage } from './state'

export class BridgeError extends Error {
  code: string
  constructor(code: string, message: string) { super(message);this.code = code }
}

// The token lives only in module memory: never in $.state, $.store or a log.
let connection: { socket: string; token: string; generation: number; launch: string } | undefined
let launches = 0
let launching = false

export function connected(): boolean {
  return connection !== undefined
}

// A bridge is starting: it is connected only once its ready line arrives.
export function starting(): boolean {
  return launching
}

export function connectionGeneration(): number | undefined {
  return connection?.generation
}

export async function request($: EngineInterface, method: 'GET' | 'POST', path: string, body?: unknown): Promise<any> {
  const conn = connection
  if (!conn) throw new BridgeError('BRIDGE_DOWN', 'NativePeers bridge is not running')
  let response
  try {
    response = await $.http.fetch('http://np-bridge' + path, { method, socketPath: conn.socket,
      headers: { Authorization: 'Bearer ' + conn.token, 'Content-Type': 'application/json' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
  } catch (error) {
    throw new BridgeError('BRIDGE_UNREACHABLE', String(error).slice(0, 200))
  }
  let envelope: any
  try { envelope = JSON.parse(response.text) } catch { throw new BridgeError('PROTOCOL_ERROR', `bridge answered HTTP ${response.status} without JSON`) }
  if (!envelope || envelope.ok !== true) throw new BridgeError(envelope?.error?.code ?? 'BRIDGE_ERROR', String(envelope?.error?.message ?? 'bridge error').slice(0, 300))
  return envelope.data
}

export async function sleep($: EngineInterface, ms: number): Promise<void> {
  await $.process.run(['/bin/sleep', String(ms / 1000)], { timeoutMs: ms + 10000 })
}

async function pump($: EngineInterface, child: AsyncGenerator<{ stream: string; text: string }, unknown>, generation: number, launchId: string): Promise<void> {
  let ended: unknown
  try {
    while (true) { const piece = await child.next();if (piece.done) { ended = piece.value;break } }
  } catch (error) {
    ended = String(error)
  }
  if (connection?.generation !== generation) return
  connection = undefined
  // After a hot reload the previous instance's bridge exits beside this one's: only its own launch's exit marks the state down.
  await bridgeExited($, launchId, { state: 'down', reason: 'bridge exited ' + JSON.stringify(ended ?? null).slice(0, 120), since: await $.clock.now() })
}

// Starts `npeer bridge` for this session and reads its one ready line. The
// child lives as long as `pump` reads it: the engine kills it when the module
// unloads or reloads.
// One start at a time. The slot is taken before the first await, the session id's included (callers pass
// it unawaited), so a second caller (a /np command arriving while activation starts the bridge, as the
// desktop's first message does) is told it is busy.
export async function launch($: EngineInterface, session: Promise<string>, cwd: string, npeer: string): Promise<{ hello: any } | { reason: string } | { busy: true }> {
  if (launching) return { busy: true }
  launching = true
  try { return await start($, await session, cwd, npeer) } finally { launching = false }
}

async function start($: EngineInterface, session: string, cwd: string, npeer: string): Promise<{ hello: any } | { reason: string }> {
  const generation = ++launches
  await setBridge($, { state: 'starting', since: await $.clock.now() })
  const child = $.process.spawn({ argv: [npeer, 'bridge', '--claude-session', session, '--protocol', '1', '--json'], cwd })
  let out = '', err = '', ready: any
  const guard = $.clock.after(15000, () => { if (!ready) void child.return(undefined as never) })
  try {
    while (!ready) {
      const piece = await child.next()
      if (piece.done) break
      if (piece.value.stream === 'stderr') { err = (err + piece.value.text).slice(-2000);continue }
      out += piece.value.text
      const line = out.indexOf('\n')
      if (line >= 0) { try { ready = JSON.parse(out.slice(0, line)) } catch { break } }
      if (out.length > 65536) break
    }
  } catch (error) {
    err = (err + String(error)).slice(-2000)
  } finally {
    guard.cancel()
  }
  if (!ready || ready.ready !== true || ready.protocol !== 1 || typeof ready.socket !== 'string' || typeof ready.token !== 'string') {
    void child.return(undefined as never)
    // A refusal arrives as {"ready":false,"error":{...}} on stdout; stderr holds anything else.
    const reason = (ready?.error?.code ? `${ready.error.code}: ${ready.error.message ?? ''}` : err.trim().split('\n').pop() || 'no ready line').slice(0, 300)
    // A connected bridge keeps the state it set: a failed start never marks it down (judged in the updater).
    if (!connection) await bridgeFailed($, { state: 'down', reason, since: await $.clock.now() })
    return { reason }
  }
  const launchId = Math.random().toString(36).slice(2, 10) + '-' + generation
  connection = { socket: ready.socket, token: ready.token, generation, launch: launchId }
  const { token: _token, ...hello } = ready
  await setBridge($, { state: 'ready', peer: hello.peer, version: hello.version, steer: hello.capabilities?.steer ?? {}, socket: ready.socket, launch: launchId, since: await $.clock.now() })
  void pump($, child, generation, launchId)
  return { hello }
}

// /np doctor's exact answer: a hook that read the state while a start was still in flight could have
// left it stale, so a live connection restores the ready state its own start set.
export async function restoreReady($: EngineInterface, hello: any, generation: number | undefined): Promise<void> {
  const socket = connection?.socket, launchId = connection?.launch
  if (!socket || connection?.generation !== generation) return
  // Only the connection that answered the hello, while it is still the live one: judged in the updater on every retry.
  await bridgeRestored($, { state: 'ready', peer: hello.peer, version: hello.version, steer: hello.capabilities?.steer ?? {}, socket, launch: launchId, since: await $.clock.now() },
    () => connection !== undefined && connection.generation === generation)
}

export async function heartbeat($: EngineInterface): Promise<void> {
  try { await request($, 'POST', '/v1/heartbeat', {}) } catch { /* the bridge exits on lost heartbeats; nothing to do here */ }
}

export async function shutdown($: EngineInterface): Promise<void> {
  try { await request($, 'POST', '/v1/shutdown', {}) } catch { /* already gone */ }
  connection = undefined
}

// The lineage long-poll. It only updates $.state: it never spawns a shadow,
// because a spawn outside a hook frame skips every hook of this plugin (G5).
export async function followLineage($: EngineInterface): Promise<void> {
  let cursor = ''
  const generation = connection?.generation
  while (connection && connection.generation === generation) {
    try {
      const data = await request($, 'GET', `/v1/lineage?wait_ms=25000${cursor ? '&cursor=' + encodeURIComponent(cursor) : ''}`)
      cursor = data.cursor ?? cursor
      if (data.changed !== false && Array.isArray(data.workers)) await setLineage($, data.workers as NpWorker[])
    } catch {
      await sleep($, 2000)
    }
  }
}
