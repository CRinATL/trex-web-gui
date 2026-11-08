import { DEFAULT_INTERVAL, DEFAULT_LAYOUT } from './chartSeries'

export type DashTabId = 'overview' | 'streams' | 'latency' | 'utilization'
export interface DashboardPrefs { tab: DashTabId; windowS: number; layout: number; yFrom0: boolean; histId: number | null }

const DEFAULTS: DashboardPrefs = Object.freeze({ tab: 'overview', windowS: DEFAULT_INTERVAL, layout: DEFAULT_LAYOUT, yFrom0: false, histId: null })

// The Dashboard's tab, chart interval, layout, y axis mode (Fit, or From 0) and the Latency histogram's PG ID: browser-side, so they
// survive leaving the page. Saved setups save and restore the tab, interval, layout and y axis mode, not the histogram PG ID (setups.ts).
// Reset on a new connect_seq (state.tsx).
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
