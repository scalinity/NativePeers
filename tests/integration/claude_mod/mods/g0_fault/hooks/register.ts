import type { Register } from 'claude-code'

// Harness-only: the main loop's first step throws mid-stream with no .catch,
// so the engine falls through to a real model request (X10). The tripwire must
// receive it.
export const register: Register = on => {
  on('turn.step', async function* g0Step($, e, next) {
    if (e.agentId === undefined && e.index === 0) {
      yield { kind: 'text', index: 0, text: 'g0: deliberate fault' }
      throw new Error('g0 deliberate fault')
    }
    return yield* next(e)
  })
}
