// Input capture for shadows: which appended rows are follow-ups, their
// provenance, and the engine framing around them. Pure: no $ here.
import type { NpInput } from '../types'

// Framings observed on Claude Code 2.1.314 (gate G3). A typed message reaches a
// running shadow as a 'delivery' attachment wrapped in <system-reminder>, a
// completed one as a 'prompt' row; the parent's SendMessage as 'coordinator'.
export const FRAMINGS = [
  { prefix: 'The user sent a new message while you were working:\n',
    suffixes: ['\n\nThis is how Claude Code surfaces messages the user sends mid-turn'] },
  { prefix: 'The coordinator sent a message while you were working:\n',
    suffixes: ['\n\nAddress this before completing your current task.'] },
]

export function stripFraming(text: string): string {
  let out = text.trim()
  const wrapped = /^<system-reminder>\s*([\s\S]*?)\s*<\/system-reminder>$/.exec(out)
  if (wrapped) out = wrapped[1] ?? out
  for (const framing of FRAMINGS) {
    if (!out.startsWith(framing.prefix)) continue
    out = out.slice(framing.prefix.length)
    for (const suffix of framing.suffixes) {
      const at = out.lastIndexOf(suffix)
      if (at >= 0) out = out.slice(0, at)
    }
    break
  }
  return out.trim()
}

export function provenance(originKind: string | undefined): NpInput['origin'] | null {
  if (originKind === 'composer') return 'human-claude-view'
  if (originKind === 'coordinator' || originKind === 'model') return 'parent-model'
  return null
}

type Row = { door: string; origin: { kind?: string }; message: { type?: string; content?: unknown } }

// A follow-up row: a user row or a delivered attachment with text and no tool
// result, by the prompt or delivery door. Never 'queue': that is the message
// queue's record of a message that also arrives as a row.
export function followUp(row: Row): { text: string; origin: NpInput['origin'] } | null {
  if (row.door !== 'prompt' && row.door !== 'delivery') return null
  if (row.message.type !== 'user' && row.message.type !== 'attachment') return null
  const origin = provenance(row.origin?.kind)
  if (!origin) return null
  const blocks = Array.isArray(row.message.content) ? row.message.content as { type?: string; text?: string }[] : []
  if (blocks.some(b => b.type === 'tool_result')) return null
  const raw = blocks.filter(b => b.type === 'text' && typeof b.text === 'string').map(b => b.text).join('\n')
  if (!raw.trim()) return null
  const text = stripFraming(raw)
  return text ? { text, origin } : null
}

// The engine's own note that a run must end with SubagentHandback, observed in sessions in auto mode:
// a 'note' row at the start of the run and an enforcement row when it ends on plain text.
export const HANDBACK_TOOL = 'SubagentHandback'
export function handbackRequired(row: Row): boolean {
  if (row.origin?.kind !== 'engine') return false
  const blocks = Array.isArray(row.message.content) ? row.message.content as { type?: string; text?: string }[] : []
  const text = typeof row.message.content === 'string' ? row.message.content : blocks.map(b => b.text ?? '').join('\n')
  return text.includes(HANDBACK_TOOL + '(')
}

export function isBrief(text: string, brief: string | undefined): boolean {
  return brief !== undefined && text.trim() === brief.trim()
}

export function unforwarded(inputs: readonly NpInput[]): NpInput[] {
  return inputs.filter(i => !i.forwarded && i.attempts < 3)
}

export function requestId(uuid: string): string {
  return 'np-in:' + uuid
}
