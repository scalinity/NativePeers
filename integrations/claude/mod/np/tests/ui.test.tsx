import { expect, test } from 'claude-code/testing'

const workers = [
  { workerId: 'W1', groupId: 'G1', generation: 1, provider: 'codex', modelRequested: 'gpt-6.1-sol', effort: 'high', title: 'Codebase review', status: 'running', startedAt: 1000, agentId: 'a1' },
  { workerId: 'W2', groupId: 'G2', generation: 1, provider: 'grok', modelRequested: 'grok-4.7', title: 'Easy wins', status: 'completed', startedAt: 1000, endedAt: 61000 },
]
const shadow = { agentId: 'a1', workerId: 'W1', groupId: 'G1', generation: 1, provider: 'codex', model: 'gpt-6.1-sol', effort: 'high', profile: 'analysis', title: 'Codebase review',
  phase: 'STREAMING', deliveryOwner: 'mirror', cursor: 4, pendingRefs: {}, inputs: [], briefConsumed: true, terminals: [], session: 's', startedAt: 1000, waits: 0, latest: 'pytest -q' }

function state(on: any, ui: Record<string, unknown> = { bandCollapsed: false, paneOpen: false }) {
  on('clock.now', () => ({ value: 61000 }))
  on('state.get', ($: any, e: any) => {
    const values: Record<string, unknown> = {
      bridge: { state: 'ready', since: 1, peer: 'np1_x' },
      lineage: { shape: 'np-lineage-1', value: workers },
      shadows: ['a1'],
      faults: { fallthrough: false },
      ui,
      shadow: e.id === 'a1' ? { shape: 'np-shadow-1', value: shadow } : undefined,
    }
    const value = values[e.key]
    return { value: value === undefined ? { value: undefined, version: 0 } : { value, version: 1 } }
  })
}

const band = (view: Record<string, string>) => ({
  plugin: 'np', component: 'AbovePrompt', requestId: 'above-prompt',
  props: { hasSurvey: false, isWorking: false, maxRows: 8, bodyColumns: 120, scroll: { offset: 0, bodyRows: 8 }, view },
}) as const

test('the band summarizes delegates and offers Stop all and Open on the terminal', async ($, on) => {
  state(on)
  const ui = await $.ui.mount({ ...band({}), surface: 'terminal' } as any)
  expect((await ui.find({ type: 'Text', text: /⟐ np · 2 delegates · 1 running · 1 done/ }))).toBeTruthy()
  expect(await ui.find({ type: 'Button', key: 'stopall' })).toBeTruthy()
  expect(await ui.find({ type: 'Button', key: 'open-W1' })).toBeTruthy()
  expect(await ui.find({ type: 'Button', key: 'open-W2' })).toBeFalsy()
  expect(await ui.find({ type: 'Text', text: /GPT-6.1 Sol high · Codebase review/ })).toBeTruthy()
  await ui.unmount()
})

test('on the desktop app the band is left to the engine: the pane beside the conversation carries it', async ($, on) => {
  state(on)
  // This hook stands for the engine's own drawing beneath the mod.
  on('ui.render', ($: any, e: any) => { const { Text } = $.ui.resolve(e);return <Text>engine</Text> })
  const ui = await $.ui.mount({ ...band({}), surface: 'desktop' } as any)
  expect((await ui.find({ type: 'Text', text: /engine/ }))?.text).toBe('engine')
  expect(await ui.find({ type: 'Text', text: /⟐ np/ })).toBeFalsy()
  await ui.unmount()
})

test('viewing a shadow shows its worker header with Interrupt, Stop and Resume cmd', async ($, on) => {
  state(on)
  const ui = await $.ui.mount({ ...band({ agentId: 'a1' }), surface: 'terminal' } as any)
  expect(await ui.find({ type: 'Text', text: /↳ Subagent of main · codex · gpt-6.1-sol · high · analysis/ })).toBeTruthy()
  for (const key of ['interrupt', 'stop', 'resume', 'pane', 'main']) expect(await ui.find({ type: 'Button', key })).toBeTruthy()
  await ui.unmount()
})

const pane = (surface: string) => ({ plugin: 'np', component: 'Pane', requestId: 'np-lineage', surface,
  props: { title: 'NativePeers', isFocused: true, bodyColumns: 100, placement: 'dock', scroll: { offset: 0, bodyRows: 30 }, view: {} } }) as any

test('the pane names workers and the selected one in words, with its ids one press away', async ($, on) => {
  state(on)
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount(pane(surface))
    expect(await ui.find({ type: 'Button', key: 'sel-W1' })).toBeTruthy()
    expect(await ui.find({ type: 'Text', text: /Model: Grok 4.7/ })).toBeTruthy()
    expect(await ui.find({ type: 'Button', key: 'p-ids' })).toBeTruthy()
    // No ids on screen, and no heading repeating the pane's own title.
    expect(await ui.find({ type: 'Text', text: /worker W2|group G2/ })).toBeFalsy()
    expect(await ui.find({ type: 'Text', text: /NativePeers lineage/ })).toBeFalsy()
    await ui.unmount()
  }
})

test('the pane offers Open on the terminal; on the desktop app it carries the summary and points to Background tasks', async ($, on) => {
  state(on, { bandCollapsed: false, paneOpen: true, selected: 'W1' })
  const terminal = await $.ui.mount(pane('terminal'))
  expect(await terminal.find({ type: 'Button', key: 'p-open' })).toBeTruthy()
  expect(await terminal.find({ type: 'Text', text: /Background tasks/ })).toBeFalsy()
  expect(await terminal.find({ type: 'Text', text: /Model: GPT-6.1 Sol, high effort/ })).toBeTruthy()
  await terminal.unmount()
  const desktop = await $.ui.mount(pane('desktop'))
  expect(await desktop.find({ type: 'Button', key: 'p-open' })).toBeFalsy()
  expect(await desktop.find({ type: 'Text', text: /Its transcript is in Background tasks/ })).toBeTruthy()
  expect(await desktop.find({ type: 'Text', text: /^2 delegates · 1 running · 1 done$/ })).toBeTruthy()
  expect(await desktop.find({ type: 'Button', key: 'stopall' })).toBeTruthy()
  await desktop.unmount()
})
