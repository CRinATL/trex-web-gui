import { DEFAULT_INTERVAL, DEFAULT_LAYOUT } from './chartSeries'

export type DashTabId = 'overview' | 'streams' | 'latency' | 'utilization'
export interface DashboardPrefs { tab: DashTabId; windowS: number; layout: number; yFrom0: boolean }

const DEFAULTS: DashboardPrefs = Object.freeze({ tab: 'overview', windowS: DEFAULT_INTERVAL, layout: DEFAULT_LAYOUT, yFrom0: false })

// The Dashboard's tab, chart interval, layout and y axis mode (Fit, or From 0): browser-side, so they survive leaving the page. Saved
// setups read the tab, interval and layout, not the y axis mode (setups.ts). Reset on a new connect_seq (state.tsx).
class DashboardPrefsStore {
  private prefs: DashboardPrefs = DEFAULTS
  private listeners = new Set<() => void>()

  private publish() { this.listeners.forEach((fn) => fn()) }

  get = (): DashboardPrefs => this.prefs
  set = (patch: Partial<DashboardPrefs>) => { this.prefs = { ...this.prefs, ...patch }; this.publish() }
  reset = () => { this.prefs = DEFAULTS; this.publish() }
  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn)
    return () => { this.listeners.delete(fn) }
  }
}

export const dashboardPrefs = new DashboardPrefsStore()
