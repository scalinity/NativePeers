export type NpiShadows = string[]

declare module 'claude-code' {
  interface PluginState {
    npi: { shadows: NpiShadows; last: string }
  }
}
