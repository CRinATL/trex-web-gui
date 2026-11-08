import { useSyncExternalStore } from 'react'
import { DEFAULT_CHARTS, DEFAULT_INTERVAL, INTERVALS, SERIES } from './chartSeries'
import type { SeriesKey } from './pgidStats'

// The Dashboard's view choices, kept per browser in localStorage (not per connection).
export interface DashView {
  bitRates: boolean; counters: boolean; history: boolean; wide: boolean
  overviewSeries: readonly string[]
  streamsMetric: SeriesKey; latencyMetric: SeriesKey
  streamsWindowS: number; latencyWindowS: number
}

export const DASH_VIEW_KEY = 'trex-web-gui.dash-view'
export const DASH_VIEW_DEFAULTS: DashView = Object.freeze({
  bitRates: false, counters: false, history: false, wide: false,
  overviewSeries: Object.freeze([...DEFAULT_CHARTS]),
  streamsMetric: 'tx_pps', latencyMetric: 'lat_avg',
  streamsWindowS: DEFAULT_INTERVAL, latencyWindowS: DEFAULT_INTERVAL,
})

const STREAMS_METRICS: readonly string[] = ['tx_pps', 'rx_pps', 'tx_bps_l1', 'tx_bps', 'rx_bps', 'loss_pps']
const LATENCY_METRICS: readonly string[] = ['lat_avg', 'lat_max', 'lat_last_max', 'lat_jitter']

// Validate one stored object field by field: each bad field takes its default.
function sanitize(raw: unknown): DashView {
  const d = DASH_VIEW_DEFAULTS
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return d
  const r = raw as Record<string, unknown>
  const bool = (k: 'bitRates' | 'counters' | 'history' | 'wide') => (typeof r[k] === 'boolean' ? (r[k] as boolean) : d[k])
  const ids = new Set(SERIES.map((s) => s.id))
  const o = r.overviewSeries
  const overviewSeries = Array.isArray(o) && o.length === DEFAULT_CHARTS.length && o.every((e) => typeof e === 'string')
    ? Object.freeze(o.map((e: string, i) => (ids.has(e) ? e : DEFAULT_CHARTS[i])))
    : d.overviewSeries
  const pick = (v: unknown, allowed: readonly string[], dflt: SeriesKey) => (typeof v === 'string' && allowed.includes(v) ? (v as SeriesKey) : dflt)
  const win = (v: unknown, dflt: number) => ((INTERVALS as readonly unknown[]).includes(v) ? (v as number) : dflt)
  return {
    bitRates: bool('bitRates'), counters: bool('counters'), history: bool('history'), wide: bool('wide'),
    overviewSeries,
    streamsMetric: pick(r.streamsMetric, STREAMS_METRICS, d.streamsMetric),
    latencyMetric: pick(r.latencyMetric, LATENCY_METRICS, d.latencyMetric),
    streamsWindowS: win(r.streamsWindowS, d.streamsWindowS),
    latencyWindowS: win(r.latencyWindowS, d.latencyWindowS),
  }
}

// The stored view, with a default for every missing or invalid field; storage that throws gives the defaults.
export function readDashView(): DashView {
  try {
    const s = localStorage.getItem(DASH_VIEW_KEY)
    return s === null ? DASH_VIEW_DEFAULTS : sanitize(JSON.parse(s))
  } catch { return DASH_VIEW_DEFAULTS }
}
const saveDashView = (v: DashView) => { try { localStorage.setItem(DASH_VIEW_KEY, JSON.stringify(v)) } catch { /* storage unavailable: the choice lasts until reload */ } }

let snap: DashView = readDashView()
const subs = new Set<() => void>()
const subscribe = (fn: () => void) => { subs.add(fn); return () => { subs.delete(fn) } }
const setView = (patch: Partial<DashView>) => {
  snap = sanitize({ ...snap, ...patch })
  saveDashView(snap)
  subs.forEach((f) => f())
}

// The Dashboard view and a setter that merges a patch, saves it and notifies every subscriber.
export function useDashView(): { view: DashView; setView: (patch: Partial<DashView>) => void } {
  const view = useSyncExternalStore(subscribe, () => snap)
  return { view, setView }
}

// Test-only: re-read the snapshot from storage and drop every listener, so one test's view cannot leak into the next.
export function resetDashViewStore() {
  snap = readDashView()
  subs.clear()
}
