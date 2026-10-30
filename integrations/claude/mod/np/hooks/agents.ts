// Agent specs, the np header grammar and labels. Pure: no $ here.
import type { NpProvider } from '../types'

export const PROVIDERS: readonly NpProvider[] = ['codex', 'claude', 'antigravity', 'grok']
export const TAGS: Record<NpProvider, string> = { codex: 'cx', claude: 'cl', antigravity: 'ag', grok: 'gk' }
export const SENTINEL = 'NativePeers mirror unavailable'
export const SHADOW_PROMPT =
  `You are a display placeholder for an external NativePeers worker. Reply exactly: "${SENTINEL} — run /np doctor." Do nothing else.`

// A model id no API serves. A shadow never calls a model; if a fault ever let a
// step through, or when the engine's own agent summary asks the agent's model,
// the request is refused before any inference (gate G1, evidence/claude-lineage-m0.json).
export function shadowModel(provider: NpProvider): string {
  return 'np-mirror-' + provider
}

export function isProvider(value: string): value is NpProvider {
  return (PROVIDERS as readonly string[]).includes(value)
}

const WHEN: Record<NpProvider, string> = {
  codex: 'a Codex CLI worker (GPT models)',
  claude: 'a separate Claude Code CLI worker',
  antigravity: 'an Antigravity CLI worker (Gemini models)',
  grok: 'a Grok Build CLI worker',
}

export function agentSpec(provider: NpProvider) {
  return {
    name: provider,
    description: `Delegate a self-contained task to ${WHEN[provider]} owned by NativePeers; its live transcript appears here. ` +
      'Start the prompt with an optional ```np {"model","effort","profile","title"}``` JSON header (call mcp__np__catalog once for exact models), ' +
      'then a brief that stands alone: the worker sees only the brief. Results arrive as a background-agent notification; follow up with SendMessage.',
    prompt: SHADOW_PROMPT,
    tools: ['mcp__np__mirror'],
    model: shadowModel(provider),
    effort: 'low',
    omitClaudeMd: true as const,
    background: true as const,
    permissionMode: 'dontAsk',
  }
}

export const HEADER_KEYS = ['model', 'effort', 'profile', 'workspace', 'title', 'fallbacks', 'quota_policy', 'requirements'] as const
export type Header = { model?: string; effort?: string; profile?: 'analysis' | 'workspace'; workspace?: string | Record<string, unknown>;
  title?: string; fallbacks?: { provider: string; model?: string; reasoning_effort?: string }[]; quota_policy?: string; requirements?: Record<string, unknown> }

// The JSON may sit on the fence lines (```np {...}```), as the compose section shows it, or between them.
const HEADER = /^\s*```np[ \t]*(?:\r?\n)?([\s\S]*?)(?:\r?\n)?[ \t]*```[ \t]*(?:\r?\n|$)/

export function parseHeader(prompt: string): { header: Header; brief: string } | { error: string } {
  const found = HEADER.exec(prompt)
  if (!found) return /^\s*```np\b/.test(prompt) ? { error: 'np header is not closed with ```' } : { header: {}, brief: prompt.trim() }
  const body = found[1] ?? ''
  if (body.length > 4096) return { error: 'np header exceeds 4 KiB' }
  let value: unknown
  try { value = JSON.parse(body) } catch { return { error: 'np header is not strict JSON' } }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return { error: 'np header must be a JSON object' }
  const header = value as Record<string, unknown>
  for (const key of Object.keys(header)) if (!(HEADER_KEYS as readonly string[]).includes(key)) return { error: `unknown np header key "${key}"` }
  for (const key of ['model', 'effort', 'title', 'quota_policy'] as const)
    if (header[key] !== undefined && (typeof header[key] !== 'string' || !(header[key] as string).trim())) return { error: `np header "${key}" must be a non-empty string` }
  if (header.profile !== undefined && header.profile !== 'analysis' && header.profile !== 'workspace') return { error: 'np header "profile" must be analysis or workspace' }
  if (header.workspace !== undefined && typeof header.workspace !== 'string' && (typeof header.workspace !== 'object' || Array.isArray(header.workspace)))
    return { error: 'np header "workspace" must be a mode string or an object' }
  if (header.fallbacks !== undefined && (!Array.isArray(header.fallbacks) || header.fallbacks.some(f => !f || typeof f !== 'object' || typeof (f as any).provider !== 'string')))
    return { error: 'np header "fallbacks" must be a list of {provider, model}' }
  if (header.requirements !== undefined && (typeof header.requirements !== 'object' || Array.isArray(header.requirements))) return { error: 'np header "requirements" must be an object' }
  if (typeof header.title === 'string' && header.title.length > 120) return { error: 'np header "title" exceeds 120 characters' }
  const brief = prompt.slice(found[0].length).trim()
  if (!brief) return { error: 'the brief after the np header is empty' }
  return { header: header as Header, brief }
}

const EFFORTS = ['minimal', 'low', 'medium', 'high', 'xhigh', 'max']

// 'gpt-6.1-sol' -> 'GPT-6.1 Sol', 'claude-sonnet-5-5' -> 'Sonnet 5.5', 'grok-4.7' -> 'Grok 4.7'.
export function modelLabel(model: string | undefined, provider: NpProvider): string {
  if (!model) return provider + ' default'
  const parts = model.replace(/\[1m\]$/, '').split('-').filter(p => p && p !== 'claude')
  const out: string[] = []
  for (const part of parts) {
    const last = out[out.length - 1]
    if (/^\d+$/.test(part) && last && /^\d+(\.\d+)*$/.test(last) && !last.includes('.')) out[out.length - 1] = last + '.' + part
    else if (part === 'gpt') out.push('GPT')
    else if (/^\d/.test(part) || EFFORTS.includes(part)) out.push(part)
    else out.push(part.charAt(0).toUpperCase() + part.slice(1))
  }
  const label = out.join(' ').replace(/^GPT (\d)/, 'GPT-$1')
  return label || model
}

export function describe(provider: NpProvider, model: string | undefined, effort: string | undefined, title: string): string {
  return `${modelLabel(model, provider)}${effort ? ' ' + effort : ''} · ${title}`.slice(0, 200)
}

export function titleFrom(header: Header, description: string | undefined, brief: string): string {
  const raw = header.title ?? (description && description.trim()) ?? brief.split('\n')[0] ?? ''
  return raw.replace(/\s+/g, ' ').trim().slice(0, 80) || 'NativePeers worker'
}

export function workerSpec(provider: NpProvider, header: Header, brief: string, title: string, cwd: string | undefined) {
  const profile = header.profile ?? 'analysis'
  const workspace = typeof header.workspace === 'string' ? { mode: header.workspace }
    : header.workspace ?? { mode: profile === 'workspace' ? 'isolated-worktree' : 'inherit-read-only' }
  return {
    provider, title, task: brief, permission_profile: profile, workspace,
    ...(header.model ? { model: header.model } : {}),
    ...(header.effort ? { reasoning_effort: header.effort } : {}),
    ...(cwd ? { cwd } : {}),
    ...(header.fallbacks ? { fallbacks: header.fallbacks } : {}),
    ...(header.quota_policy ? { quota_policy: header.quota_policy } : {}),
    ...(header.requirements ? { requirements: header.requirements } : {}),
  }
}

export function adoptionBrief(title: string, provider: string, model: string | undefined, effort: string | undefined, groupId: string, task: string): string {
  return `NativePeers worker "${title}" (${provider} ${model ?? 'default'}${effort ? ' ' + effort : ''}), group ${groupId}.\n\nTask:\n${task}`
}
