import type { Register } from 'claude-code'

// A separate plugin observes calls the scripted driver raised; an originating
// hook's own plugin is skipped by the engine on those nested calls.
export const register: Register = on => {
  on('agent.spawn', async ($, e, next) => {
    const r = await next(e)
    const directory = await $.env.get('NP_DRIVER_SPAWN_LOG')
    if (directory) await $.fs.write(`${directory}/${e.tool_use_id}.json`, JSON.stringify({ session: await $.session.id(), type: e.subagentType, name: e.name, isTeammate: e.isTeammate === true, parentAgentId: e.parentAgentId, workflow: Boolean(e.workflow), agentId: r.agentId, deny: r.deny }))
    return r
  })
}
