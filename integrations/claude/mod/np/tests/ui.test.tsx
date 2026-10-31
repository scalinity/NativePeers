import { expect, test } from 'claude-code/testing'
import { controlTarget } from '../hooks/ui'

const workers = [
  { workerId: 'W1', groupId: 'G1', generation: 1, provider: 'codex', modelRequested: 'gpt-6.1-sol', effort: 'high', title: 'Codebase review', status: 'running', startedAt: 1000, agentId: 'a1' },
  { workerId: 'W2', groupId: 'G2', generation: 1, provider: 'grok', modelRequested: 'grok-4.7', title: 'Easy wins', status: 'completed', startedAt: 1000, endedAt: 61000 },
]
const shadow = { agentId: 'a1', workerId: 'W1', groupId: 'G1', generation: 1, provider: 'codex', model: 'gpt-6.1-sol', effort: 'high', profile: 'analysis', title: 'Codebase review',
  phase: 'STREAMING', deliveryOwner: 'mirror', cursor: 4, pendingRefs: {}, inputs: [], briefConsumed: true, terminals: [], session: 's', startedAt: 1000, waits: 0, latest: 'pytest -q' }

const conversation = { workerId: 'W1', cursor: 7, rows: [
  { kind: 'task', text: 'List the files in this folder.' },
  { kind: 'tool', text: 'ls -A1', status: 'exit 0', detail: '.git\nREADME.md' },
  { kind: 'text', text: 'It holds .git and README.md.' },
  { kind: 'you', text: 'Also count them.', item: 'in:r1' },
] }

function state(on: any, ui: Record<string, unknown> = { bandCollapsed: false, paneOpen: false }, held: Record<string, unknown> = shadow) {
  on('clock.now', () => ({ value: 61000 }))
  on('state.get', ($: any, e: any) => {
    const values: Record<string, unknown> = {
      bridge: { state: 'ready', since: 1, peer: 'np1_x' },
      lineage: { shape: 'np-lineage-1', value: workers },
      shadows: ['a1'],
      faults: { fallthrough: false },
      ui,
      shadow: e.id === 'a1' ? { shape: 'np-shadow-1', value: held } : undefined,
      transcript: e.id === 'W1' ? { shape: 'np-transcript-1', value: conversation } : undefined,
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

test('on the desktop app the band is left to the engine while the pane beside the conversation is open', async ($, on) => {
  state(on, { bandCollapsed: false, paneOpen: true })
  on('ui.panes', () => ({ value: [{ id: 'np-lineage', title: 'NativePeers', isShown: true, isFocused: false, isPlaced: true }] }))
  // This hook stands for the engine's own drawing beneath the mod.
  on('ui.render', ($: any, e: any) => { const { Text } = $.ui.resolve(e);return <Text>engine</Text> })
  const ui = await $.ui.mount({ ...band({}), surface: 'desktop' } as any)
  expect((await ui.find({ type: 'Text', text: /engine/ }))?.text).toBe('engine')
  expect(await ui.find({ type: 'Text', text: /⟐ np/ })).toBeFalsy()
  await ui.unmount()
})

test('on the desktop app a closed pane is offered back in one line above the prompt', async ($, on) => {
  state(on, { bandCollapsed: false, paneOpen: false })
  on('ui.panes', () => ({ value: [] }))
  const ui = await $.ui.mount({ ...band({}), surface: 'desktop' } as any)
  expect(await ui.find({ type: 'Text', text: /⟐ np · 2 delegates · 1 running · 1 done/ })).toBeTruthy()
  expect(await ui.find({ type: 'Button', key: 'reopen' })).toBeTruthy()
  // Only the way back: no Stop all, Pane or Open rows in this line.
  for (const key of ['stopall', 'pane', 'open-W1']) expect(await ui.find({ type: 'Button', key })).toBeFalsy()
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

test('the pane lists workers by title, each opening its conversation; on the desktop app it carries the summary', async ($, on) => {
  state(on)
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount(pane(surface))
    for (const key of ['sel-W1', 'sel-W2']) expect(await ui.find({ type: 'Button', key })).toBeTruthy()
    expect(await ui.find({ type: 'Text', text: /Codex · Running · 1m00s/ })).toBeTruthy()
    expect(await ui.find({ type: 'Text', text: /Press a worker to read its conversation and message it/ })).toBeTruthy()
    // No ids, no heading repeating the pane's title, no resume command and no pointer elsewhere.
    expect(await ui.find({ type: 'Text', text: /worker W2|group G2|NativePeers lineage|Background tasks/ })).toBeFalsy()
    expect(await ui.find({ type: 'Button', key: 'resume' })).toBeFalsy()
    expect(Boolean(await ui.find({ type: 'Text', text: /^2 delegates · 1 running · 1 done$/ }))).toBe(surface === 'desktop')
    await ui.unmount()
  }
})

test('a worker\'s conversation shows what it was asked, said and did, with a field to message it and its controls', async ($, on) => {
  state(on, { bandCollapsed: false, paneOpen: true, viewing: 'W1', sent: 2 })
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount(pane(surface))
    expect(await ui.find({ type: 'Button', key: 'back' })).toBeTruthy()
    expect(await ui.find({ type: 'Text', text: /^Codebase review$/ })).toBeTruthy()
    expect(await ui.find({ type: 'Text', text: /Model: GPT-6.1 Sol, high effort/ })).toBeTruthy()
    for (const text of [/Task from main/, /▸ ls -A1 · exit 0/, /^Codex$/, /^You$/]) expect(await ui.find({ type: 'Text', text })).toBeTruthy()
    const field: any = await ui.find({ type: 'Input', key: 'say-2' })
    expect(field).toBeTruthy()
    // The desktop app rings a focused field: it is not focused there until the person picks it.
    expect(Boolean(field?.props?.autoFocus ?? field?.autoFocus)).toBe(surface !== 'desktop')
    for (const key of ['p-interrupt', 'p-stop', 'p-ids']) expect(await ui.find({ type: 'Button', key })).toBeTruthy()
    expect(await ui.find({ type: 'Button', key: 'attach:W1' })).toBeFalsy()
    await ui.unmount()
  }
})

test('a running worker whose shadow faulted is offered Attach in its conversation', async ($, on) => {
  state(on, { bandCollapsed: false, paneOpen: true, viewing: 'W1' }, { ...shadow, phase: 'FAULTED' })
  const ui = await $.ui.mount(pane('desktop'))
  expect(await ui.find({ type: 'Button', key: 'attach:W1' })).toBeTruthy()
  await ui.unmount()
})

test('a finished worker\'s conversation offers no Interrupt or Stop, and says when it is still loading', async ($, on) => {
  state(on, { bandCollapsed: false, paneOpen: true, viewing: 'W2' })
  const ui = await $.ui.mount(pane('desktop'))
  for (const key of ['p-interrupt', 'p-stop']) expect(await ui.find({ type: 'Button', key })).toBeFalsy()
  expect(await ui.find({ type: 'Text', text: /Loading the conversation/ })).toBeTruthy()
  await ui.unmount()
})

test('the pane\'s worker controls name the lineage row\'s worker while its shadow is still binding', () => {
  const row = workers[0] as any
  expect(controlTarget(row, { ...shadow, workerId: undefined, phase: 'BINDING' } as any).workerId).toBe('W1')
  expect(controlTarget(row, undefined)).toMatchObject({ workerId: 'W1', generation: 1, provider: 'codex' })
  expect(controlTarget(row, shadow as any).agentId).toBe('a1')
})

test('a refused message returns to the field, and an uncertain one offers Retry', async ($, on) => {
  state(on, { bandCollapsed: false, paneOpen: true, viewing: 'W1', sent: 3, drafts: { W1: { workerId: 'W1', text: 'Also count them', requestId: 'np-pane:W1:1', state: 'refused', reason: 'TARGET_OFFLINE' } } })
  const refused = await $.ui.mount(pane('desktop'))
  expect((await refused.find({ type: 'Input', key: 'say-3' }) as any)?.props?.value ?? (await refused.find({ type: 'Input', key: 'say-3' }) as any)?.value).toBe('Also count them')
  expect(await refused.find({ type: 'Text', text: /Not delivered: TARGET_OFFLINE/ })).toBeTruthy()
  expect(await refused.find({ type: 'Button', key: 'p-retry' })).toBeFalsy()
  await refused.unmount()
})

test('an uncertain message offers Retry under its own request', async ($, on) => {
  state(on, { bandCollapsed: false, paneOpen: true, viewing: 'W1', sent: 3, drafts: { W1: { workerId: 'W1', text: 'Also count them', requestId: 'np-pane:W1:1', state: 'uncertain', reason: 'socket closed' } } })
  const ui = await $.ui.mount(pane('desktop'))
  expect(await ui.find({ type: 'Button', key: 'p-retry' })).toBeTruthy()
  expect(await ui.find({ type: 'Text', text: /Delivery uncertain: socket closed/ })).toBeTruthy()
  await ui.unmount()
})

test('a draft belongs to its worker: another worker\'s unresolved message shows nothing here', async ($, on) => {
  state(on, { bandCollapsed: false, paneOpen: true, viewing: 'W1', sent: 1, drafts: { W2: { workerId: 'W2', text: 'Elsewhere', requestId: 'np-pane:W2:1', state: 'uncertain' } } })
  const ui = await $.ui.mount(pane('desktop'))
  expect(await ui.find({ type: 'Button', key: 'p-retry' })).toBeFalsy()
  expect(await ui.find({ type: 'Text', text: /Delivery uncertain|Sending/ })).toBeFalsy()
  await ui.unmount()
})
