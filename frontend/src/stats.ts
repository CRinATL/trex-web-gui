import type { SessionState, StatsMsg } from './api'
import { COUNTER_KEYS } from './statsFormat'

// One stored message's record for a single scope, plus its ts.
export type Point = { ts: number } & Record<string, number | null>
// getSnapshot must hand back the same object until the next change, or useSyncExternalStore loops.
export type Snapshot = { latest: StatsMsg | null; version: number }

export const CAPACITY = 300

class StatsStore {
  private buf: StatsMsg[] = []
  private snap: Snapshot = { latest: null, version: 0 }
  private listeners = new Set<() => void>()
  private arrived: number | null = null
  private prevSession: SessionState = 'disconnected'
  // scope ('global', 'total' or a port id) -> key -> counter value at the last Clear; browser-side only, no RPC.
  private baselines: Record<string, Record<string, number>> = {}
  // stats_epoch of the last message: the server bumps it on every Reset it enters, from any browser.
  private epoch: number | null = null

  private publish() {
    this.snap = { latest: this.buf.length ? this.buf[this.buf.length - 1] : null, version: this.snap.version + 1 }
    this.listeners.forEach((fn) => fn())
  }

  push = (msg: StatsMsg) => {
    // A Reset zeroed the server's counters, so every baseline is stale, including one that the regrown counters
    // have already passed (which rebaseDrops cannot see). The first epoch seen is not a change.
    if (typeof msg.stats_epoch === 'number') {
      if (this.epoch !== null && msg.stats_epoch !== this.epoch) this.baselines = {}
      this.epoch = msg.stats_epoch
    }
    this.buf.push(msg)
    this.arrived = performance.now()
    if (this.buf.length > CAPACITY) this.buf.splice(0, this.buf.length - CAPACITY)
    this.rebaseDrops(msg)
    this.publish()
  }

  // Resets on a transition into connecting or disconnected; the store keeps the previous state itself.
  // Mirrors the reducer's transition rule (state.tsx, the `session` case); change both together.
  onSession = (state: SessionState) => {
    const prev = this.prevSession
    this.prevSession = state
    if (state !== prev && (state === 'connecting' || state === 'disconnected')) this.reset()
  }

  // Drops everything the server sent: the buffer, the baselines and the epoch.
  reset = () => {
    this.buf = []
    this.arrived = null
    this.baselines = {}
    this.epoch = null
    this.publish()
  }

  // raw < baseline means the client's own counters were zeroed (a GUI Reset or a reconnect): that baseline drops
  // to 0. Done here, not in relative(), so it happens whether or not any cell is mounted.
  // A Reset whose counters regrow past the old baseline within one interval is not seen here; stats_epoch
  // (push, above) closes that case by dropping every baseline when it changes.
  private rebaseDrops(m: StatsMsg) {
    for (const [scope, b] of Object.entries(this.baselines)) {
      const rec = this.scopeRecord(m, scope)
      if (!rec) continue
      for (const k of Object.keys(b)) { const v = rec[k]; if (typeof v === 'number' && v < b[k]) b[k] = 0 }
    }
  }

  private scopeRecord(m: StatsMsg, scope: string): Record<string, number | null> | undefined {
    return scope === 'global' ? m.global : scope === 'total' ? m.total : m.ports[scope]
  }

  // Records the latest COUNTER_KEYS values as baselines. Sends nothing to the server, so trex-console is unaffected.
  clear = () => {
    const m = this.snap.latest
    if (!m) return
    const next: Record<string, Record<string, number>> = {}
    // 'total' has no baseline of its own: relative('total') sums the ports, so a per-port Reset cannot split them.
    for (const scope of ['global', ...Object.keys(m.ports)]) {
      const rec = this.scopeRecord(m, scope)
      if (!rec) continue
      const b: Record<string, number> = {}
      for (const k of COUNTER_KEYS) { const v = rec[k]; if (typeof v === 'number') b[k] = v }   // a counter that is null at Clear gets no baseline
      next[scope] = b
    }
    this.baselines = next
    this.publish()
  }

  // Counter minus its baseline (pure read). A key outside COUNTER_KEYS is returned raw. For 'total' a counter is the
  // sum of the ports' relative values (null parts skipped, null only if all are null), once a Clear has recorded baselines.
  relative = (scope: string, key: string): number | null => {
    const m = this.snap.latest
    if (!m) return null
    if (!(COUNTER_KEYS as readonly string[]).includes(key)) return this.scopeRecord(m, scope)?.[key] ?? null
    if (scope === 'total' && Object.keys(this.baselines).length) {
      let sum: number | null = null
      for (const id of Object.keys(m.ports)) {
        const v = this.relative(id, key)
        if (v !== null) sum = (sum ?? 0) + v
      }
      return sum
    }
    const raw = this.scopeRecord(m, scope)?.[key] ?? null
    const base = this.baselines[scope]?.[key]
    return raw === null || base === undefined ? raw : raw - base
  }

  latest = (): StatsMsg | null => this.snap.latest
  // performance.now() when the newest message arrived; not in Snapshot, so selectors do not re-render on it.
  receivedAt = (): number | null => this.arrived
  version = (): number => this.snap.version
  snapshot = (): Snapshot => this.snap

  // key: 'global', 'total' or a port id string. Points with ts > latest.ts - windowS, oldest first.
  series = (key: string, windowS: number): Point[] => {
    const last = this.snap.latest
    if (!last) return []
    const out: Point[] = []
    for (const m of this.buf) {
      if (!(m.ts > last.ts - windowS)) continue
      const rec = key === 'global' ? m.global : key === 'total' ? m.total : m.ports[key]
      if (rec) out.push({ ...rec, ts: m.ts })
    }
    return out
  }

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn)
    return () => { this.listeners.delete(fn) }
  }
}

export const statsStore = new StatsStore()
