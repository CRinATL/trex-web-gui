import { useSyncExternalStore } from 'react'
import type { TestSetup } from './api'
import type { LoadResult, RunResult } from './setups'

// The Run and Load state of the setups feature, apart from setups.ts so that state.tsx can reset it without importing
// setups.ts (which reaches portLabels.ts and so state.tsx again). Type imports only.

// Whether a Run is in progress in this tab. It guards this tab only: another tab's Run meets the server's ownership refusals.
class RunInProgress {
  private on = false
  private listeners = new Set<() => void>()
  get = (): boolean => this.on
  set = (on: boolean) => { if (on !== this.on) { this.on = on; this.listeners.forEach((fn) => fn()) } }
  reset = () => this.set(false)
  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn)
    return () => { this.listeners.delete(fn) }
  }
}
export const runInProgress = new RunInProgress()
export const useRunInProgress = (): boolean => useSyncExternalStore(runInProgress.subscribe, runInProgress.get)

export interface SetupSession {
  setup: TestSetup | null; loaded: LoadResult | null; status: Record<number, string>;   // keyed by the setup port's index (two setup ports can name one server id)
  result: RunResult | null; error: string | null
}
const NO_SESSION: SetupSession = Object.freeze({ setup: null, loaded: null, status: Object.freeze({}), result: null, error: null })

// The last Load and Run of the setups dialog. It lives here so that closing the dialog (the page went inactive, say) while a
// Run goes on loses neither the progress nor the result: reopening shows them. Reset on a new connect_seq (state.tsx).
class SetupSessionStore {
  private cur: SetupSession = NO_SESSION
  private listeners = new Set<() => void>()
  get = (): SetupSession => this.cur
  set = (patch: Partial<SetupSession>) => { this.cur = { ...this.cur, ...patch }; this.listeners.forEach((fn) => fn()) }
  private gen = 0
  // Counts resets. A Load or Run captures it when it starts and writes through `update`, so what it reports after a
  // connect_seq reset (status, result, error) is dropped instead of landing in the new connection's session.
  epoch = (): number => this.gen
  update = (epoch: number, patch: Partial<SetupSession> | ((cur: SetupSession) => Partial<SetupSession>)) => {
    if (epoch === this.gen) this.set(typeof patch === 'function' ? patch(this.cur) : patch)
  }
  reset = () => { this.cur = NO_SESSION; this.gen++; this.listeners.forEach((fn) => fn()) }
  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn)
    return () => { this.listeners.delete(fn) }
  }
}
export const setupSession = new SetupSessionStore()
export const useSetupSession = (): SetupSession => useSyncExternalStore(setupSession.subscribe, setupSession.get)
