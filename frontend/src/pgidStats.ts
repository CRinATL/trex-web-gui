import type { PgidMsg, SessionState } from './api'
import { AUTO_SELECT, PGID_PALETTE } from './pgidPalette'

export const PGID_CAPACITY = 300
export const TRACK_MAX = 8        // chart history pg_ids per tab; two tabs, so at most 16 in all
export const WINDOW_LEN = 10      // the latency table's last_max window
// Same value as MAX_PGIDS in backend/trex_web_gui/trexapi/stats.py: the ids the backend queries, ascending.
export const MAX_PGIDS = 1151

export type Tab = 'streams' | 'latency'
export type FlowCounter = 'tx_pkts' | 'rx_pkts' | 'tx_bytes' | 'rx_bytes'
export type ErrCounter = 'dropped' | 'dup' | 'out_of_order' | 'seq_too_high' | 'seq_too_low'
export type SeriesKey = 'tx_pps' | 'rx_pps' | 'tx_bps_l1' | 'tx_bps' | 'rx_bps' | 'loss_pps' | 'lat_avg' | 'lat_jitter' | 'lat_last_max' | 'lat_max'
export type GlobalErr = 'rx_err' | 'tx_err'
export type PgidPoint = { ts: number; v: number | null }

// The chart selection of one tab: the picked pg_ids in the order picked, each with its colour; `seeded` once the first
// AUTO_SELECT have been picked.
export interface PgidSelection { picked: { id: number; color: string }[]; seeded: boolean }
const NO_SELECTION: PgidSelection = { picked: [], seeded: false }

// The selection that `ids` (the tab's pg_ids, ascending) leave of `sel`: the first AUTO_SELECT are picked the first time any
// ids are seen; an id that is gone drops out and frees its colour; no ids at all (streams removed, or a reconnect) means
// the next ones seed afresh. Returns `sel` itself when nothing changes.
export function reconcileSelection(sel: PgidSelection, ids: number[]): PgidSelection {
  let { picked, seeded } = sel
  if (!seeded && ids.length > 0) {
    seeded = true
    picked = ids.slice(0, AUTO_SELECT).map((id, i) => ({ id, color: PGID_PALETTE[i] }))
  }
  const present = new Set(ids)
  const kept = picked.filter((p) => present.has(p.id))
  if (kept.length !== picked.length) picked = kept
  if (seeded && ids.length === 0) seeded = false
  return picked === sel.picked && seeded === sel.seeded ? sel : { picked, seeded }
}

const FLOW_COUNTERS: readonly FlowCounter[] = ['tx_pkts', 'rx_pkts', 'tx_bytes', 'rx_bytes']

// Counters at the last Clear for one pg_id; browser-side only. flow: field -> scope -> value.
interface Baseline { flow: Record<string, Record<string, number>>; err: Record<string, number>; hist: Record<string, number> }
const emptyBaseline = (): Baseline => ({ flow: {}, err: {}, hist: {} })

interface PgState {
  verId: number | undefined
  base: Baseline
  window: number[]               // newest first
  max: number | null             // running max of last_max
}

const num = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

// Tx pps minus Rx pps, floored at 0; null when either is missing.
const lossRate = (tx: unknown, rx: unknown): number | null => (num(tx) && num(rx) ? Math.max(0, tx - rx) : null)

// Absolute server counters, baselined here so Clear is browser-side (no RPC, trex-console unaffected). Rates are the
// server's own and are never baselined. Chart history is kept only for the tracked pg_ids; the latency window and
// running max are kept for every latency pg_id.
class PgidStore {
  private msg: PgidMsg | null = null
  private arrived: number | null = null
  private ver = 0
  private listeners = new Set<() => void>()
  private prevSession: SessionState = 'disconnected'
  private pgs = new Map<number, PgState>()
  private history = new Map<number, Record<SeriesKey, PgidPoint[]>>()
  private tracked: Record<Tab, number[]> = { streams: [], latency: [] }
  // The chart selection per tab. It lives here, not in a component, so it survives leaving the Dashboard page.
  private selections: Record<Tab, PgidSelection> = { streams: NO_SELECTION, latency: NO_SELECTION }
  // flow_global counters at the last Clear: name -> scope -> value.
  private globalBase: Record<GlobalErr, Record<string, number>> = { rx_err: {}, tx_err: {} }

  private publish() {
    this.ver++
    this.listeners.forEach((fn) => fn())
  }

  private state(id: number): PgState {
    let s = this.pgs.get(id)
    if (!s) { s = { verId: undefined, base: emptyBaseline(), window: [], max: null }; this.pgs.set(id, s) }
    return s
  }

  private isTracked = (id: number) => this.tracked.streams.includes(id) || this.tracked.latency.includes(id)

  push = (m: PgidMsg) => {
    this.msg = m
    this.arrived = performance.now()
    for (const key of Object.keys(m.flow)) {
      const id = Number(key)
      const s = this.state(id)
      const ver = m.ver_id[key]
      // A changed ver_id means a different stream now owns this id: nothing of the old one carries over.
      // No entry for the id: no comparison, and the last known ver_id stays.
      if (ver !== undefined) {
        if (s.verId !== undefined && ver !== s.verId) { s.base = emptyBaseline(); s.window = []; s.max = null; this.history.delete(id) }
        s.verId = ver
      }
      this.rebaseDrops(m, key, s.base)
      const lm = m.latency[key]?.latency.last_max
      if (num(lm)) {
        s.window.unshift(lm)
        if (s.window.length > WINDOW_LEN) s.window.length = WINDOW_LEN
        if (s.max === null || lm > s.max) s.max = lm
      }
      if (this.isTracked(id)) this.record(id, m, s)
    }
    // Same rule for flow_global: a counter below its baseline was zeroed by the server.
    for (const name of ['rx_err', 'tx_err'] as const) {
      const base = this.globalBase[name]
      for (const scope of Object.keys(base)) {
        const raw = m.flow_global?.[name]?.[scope]
        if (num(raw) && raw < base[scope]) base[scope] = 0
      }
    }
    // Bounded over a long session: state for an id the message neither carries nor versions is gone.
    for (const id of [...this.pgs.keys()]) if (!(String(id) in m.flow) && !(String(id) in m.ver_id)) { this.pgs.delete(id); this.history.delete(id) }
    this.publish()
  }

  // raw < baseline means the server zeroed that counter under the same ver_id: that baseline drops to 0 (P3-min's
  // rule). The five error counters are exempt: the server decrements `dropped` when a late packet arrives
  // (get_pgid_stats docstring, scenario 2), so their baselines change only on Clear, ver_id or connect_seq.
  private rebaseDrops(m: PgidMsg, key: string, b: Baseline) {
    for (const [field, scopes] of Object.entries(b.flow)) {
      for (const scope of Object.keys(scopes)) {
        const raw = m.flow[key]?.[field]?.[scope]
        if (num(raw) && raw < scopes[scope]) scopes[scope] = 0
      }
    }
    const hist = m.latency[key]?.latency.histogram
    if (hist) for (const k of Object.keys(b.hist)) if (num(hist[k]) && hist[k] < b.hist[k]) b.hist[k] = 0
  }

  private record(id: number, m: PgidMsg, s: PgState) {
    const key = String(id)
    const f = m.flow[key]
    const lat = m.latency[key]?.latency
    const total = (k: string) => { const v = f?.[k]?.total; return num(v) ? v : null }
    const v: Record<SeriesKey, number | null> = {
      tx_pps: total('tx_pps'), rx_pps: total('rx_pps'), tx_bps_l1: total('tx_bps_l1'), tx_bps: total('tx_bps'), rx_bps: total('rx_bps'),
      loss_pps: lossRate(f?.tx_pps?.total, f?.rx_pps?.total),
      lat_avg: num(lat?.average) ? lat.average : null,
      lat_jitter: num(lat?.jitter) ? lat.jitter : null,
      lat_last_max: num(lat?.last_max) ? lat.last_max : null,
      lat_max: s.max,
    }
    let h = this.history.get(id)
    if (!h) {
      h = { tx_pps: [], rx_pps: [], tx_bps_l1: [], tx_bps: [], rx_bps: [], loss_pps: [], lat_avg: [], lat_jitter: [], lat_last_max: [], lat_max: [] }
      this.history.set(id, h)
    }
    for (const k of Object.keys(v) as SeriesKey[]) {
      h[k].push({ ts: m.ts, v: v[k] })
      if (h[k].length > PGID_CAPACITY) h[k].splice(0, h[k].length - PGID_CAPACITY)
    }
  }

  // Resets on a transition into connecting or disconnected; the store keeps the previous state itself.
  onSession = (state: SessionState) => {
    const prev = this.prevSession
    this.prevSession = state
    if (state !== prev && (state === 'connecting' || state === 'disconnected')) this.reset()
  }

  // Drops everything the server sent, the chart selections and the tracked pg_ids (they belong to the old session). Navigating
  // away from the Dashboard does not reset: the selection and the chart history stay.
  reset = () => {
    this.selections = { streams: NO_SELECTION, latency: NO_SELECTION }
    this.tracked = { streams: [], latency: [] }
    this.msg = null
    this.arrived = null
    this.pgs.clear()
    this.history.clear()
    this.globalBase = { rx_err: {}, tx_err: {} }
    this.publish()
  }

  // Records the latest counters as baselines and empties every latency window and running max.
  clear = () => {
    const m = this.msg
    if (!m) return
    for (const name of ['rx_err', 'tx_err'] as const) {
      const rec: Record<string, number> = {}
      for (const [scope, v] of Object.entries(m.flow_global?.[name] ?? {})) if (num(v)) rec[scope] = v
      this.globalBase[name] = rec
    }
    for (const key of Object.keys(m.flow)) {
      const s = this.state(Number(key))
      const b = emptyBaseline()
      for (const field of FLOW_COUNTERS) {
        const rec: Record<string, number> = {}
        for (const [scope, v] of Object.entries(m.flow[key][field] ?? {})) if (num(v)) rec[scope] = v
        b.flow[field] = rec
      }
      const lat = m.latency[key]
      if (lat) {
        for (const [k, v] of Object.entries(lat.err_cntrs)) if (num(v)) b.err[k] = v
        for (const [k, v] of Object.entries(lat.latency.histogram)) if (num(v)) b.hist[k] = v
      }
      s.base = b
      s.window = []
      s.max = null
    }
    this.publish()
  }

  // The at most TRACK_MAX pg_ids of one tab that keep chart history. Others lose theirs at once.
  track = (tab: Tab, ids: number[]) => {
    this.tracked[tab] = ids.slice(0, TRACK_MAX)
    for (const id of [...this.history.keys()]) if (!this.isTracked(id)) this.history.delete(id)
  }

  // The chart selection of a tab; the same object until setSelection changes it, so it is a stable snapshot.
  selection = (tab: Tab): PgidSelection => this.selections[tab]

  // Stores the selection and tracks its pg_ids (at most TRACK_MAX).
  setSelection = (tab: Tab, sel: PgidSelection) => {
    this.selections = { ...this.selections, [tab]: sel }
    this.track(tab, sel.picked.map((p) => p.id))
    this.publish()
  }

  // Points with ts > latest.ts - windowS, oldest first. Empty for a pg_id that is not tracked.
  series = (pgid: number, key: SeriesKey, windowS: number): PgidPoint[] => {
    const last = this.msg
    if (!last) return []
    return (this.history.get(pgid)?.[key] ?? []).filter((p) => p.ts > last.ts - windowS)
  }

  // Counter minus its baseline. scope: a port id or 'total'. null when the server sent none.
  relative = (pgid: number, field: FlowCounter, scope: string): number | null => {
    const raw = this.msg?.flow[String(pgid)]?.[field]?.[scope]
    if (!num(raw)) return null
    return raw - (this.pgs.get(pgid)?.base.flow[field]?.[scope] ?? 0)
  }

  // The server's Tx pps minus Rx pps (total scope), floored at 0; null when either is missing. The charted loss_pps series.
  lossPps = (pgid: number): number | null => {
    const f = this.msg?.flow[String(pgid)]
    return lossRate(f?.tx_pps?.total, f?.rx_pps?.total)
  }

  // flow_global counter minus its baseline (Clear). scope: a port id. null when the server sent none.
  relativeFlowGlobal = (name: GlobalErr, scope: string): number | null => {
    const raw = this.msg?.flow_global?.[name]?.[scope]
    return num(raw) ? raw - (this.globalBase[name][scope] ?? 0) : null
  }

  // Tx minus Rx packets since Clear, floored at 0 (a packet in flight is not loss).
  lossPkts = (pgid: number): number | null => {
    const tx = this.relative(pgid, 'tx_pkts', 'total')
    const rx = this.relative(pgid, 'rx_pkts', 'total')
    return tx === null || rx === null ? null : Math.max(0, tx - rx)
  }

  // null while the pg_id has no latency entry (no samples yet). Floored at 0: `dropped` can decrease.
  relativeErr = (pgid: number, name: ErrCounter): number | null => {
    const raw = this.msg?.latency[String(pgid)]?.err_cntrs[name]
    if (!num(raw)) return null
    return Math.max(0, raw - (this.pgs.get(pgid)?.base.err[name] ?? 0))
  }

  // Histogram counts since Clear, by bucket (µs, as a string). {} while there are no samples.
  relativeHist = (pgid: number): Record<string, number> => {
    const hist = this.msg?.latency[String(pgid)]?.latency.histogram
    const base = this.pgs.get(pgid)?.base.hist
    const out: Record<string, number> = {}
    if (hist) for (const [k, v] of Object.entries(hist)) out[k] = Math.max(0, v - (base?.[k] ?? 0))
    return out
  }

  // The last WINDOW_LEN last_max values, newest first.
  window = (pgid: number): number[] => this.pgs.get(pgid)?.window ?? []
  // The largest last_max since connect, Clear or a ver_id change; null if none yet.
  runningMax = (pgid: number): number | null => this.pgs.get(pgid)?.max ?? null

  // Ascending, both types.
  ids = (): number[] => {
    const a = this.msg?.active
    return a ? [...new Set([...a.flow_stats, ...a.latency])].sort((x, y) => x - y) : []
  }
  latencyIds = (): number[] => [...(this.msg?.active.latency ?? [])].sort((x, y) => x - y)

  latest = (): PgidMsg | null => this.msg
  // performance.now() when the newest message arrived; not in a snapshot, so selectors do not re-render on it.
  receivedAt = (): number | null => this.arrived
  version = (): number => this.ver

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn)
    return () => { this.listeners.delete(fn) }
  }
}

export const pgidStore = new PgidStore()
