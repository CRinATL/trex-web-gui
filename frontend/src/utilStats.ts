import type { SessionState, UtilMsg } from './api'

export const UTIL_CAPACITY = 300
export type UtilPoint = { ts: number; v: number | null }

type Hist = (number | null)[]
const same = (a: Hist, b: Hist) => a.length === b.length && a.every((x, i) => x === b[i])

// How many of h's newest samples are new since prev (both newest first, 20 long). The server samples about once a
// second and a poll sees 0, 1, 2, 3 or 10 new ones (tests/fixtures/p3a/rpc.jsonl), so the answer is the smallest
// d >= 1 with h[d:] == prev[:len-d]. An unchanged non-constant history has none (0). A constant history matches at
// every shift (an idle core), so the elapsed seconds since the last point decide. No shift fits: all of h is new.
export function newSamples(h: Hist, prev: Hist, ts: number, lastTs: number): number {
  const len = h.length
  if (prev.length !== len) return len
  if (same(h, prev)) {
    if (!h.every((x) => x === h[0])) return 0
    return Math.min(len, Math.max(0, Math.round(ts - lastTs)))
  }
  for (let d = 1; d < len; d++) if (same(h.slice(d), prev.slice(0, len - d))) return d
  return len
}

// Per-core CPU history for the Utilization tab. The server reports each core's last 20 samples, newest first, with
// no timestamps, so the first message seeds a series one point per second back from its ts and each later message
// appends the samples that are new since the previous message (newSamples).
class UtilStore {
  private msg: UtilMsg | null = null
  private arrived: number | null = null
  private cores: UtilPoint[][] = []
  private prev: Hist[] = []
  private prevSession: SessionState = 'disconnected'
  private ver = 0
  private listeners = new Set<() => void>()

  private publish() {
    this.ver++
    this.listeners.forEach((fn) => fn())
  }

  push = (m: UtilMsg) => {
    m.cpu.forEach((core, i) => {
      const h = core.history
      const out = (this.cores[i] ??= [])
      if (!this.prev[i]) {
        out.splice(0, out.length, ...h.map((v, j) => ({ ts: m.ts - j, v })).reverse())   // index 0 is the newest: it goes last
      } else {
        const d = newSamples(h, this.prev[i], m.ts, out.length ? out[out.length - 1].ts : m.ts)
        // ts must strictly increase (the chart's x axis): an overlapping old point is skipped.
        for (let j = d - 1; j >= 0; j--) if (!out.length || m.ts - j > out[out.length - 1].ts) out.push({ ts: m.ts - j, v: h[j] })
      }
      if (out.length > UTIL_CAPACITY) out.splice(0, out.length - UTIL_CAPACITY)
      this.prev[i] = h
    })
    this.msg = m
    this.arrived = performance.now()
    this.publish()
  }

  // Resets on a transition into connecting or disconnected; the store keeps the previous state itself.
  onSession = (state: SessionState) => {
    const prev = this.prevSession
    this.prevSession = state
    if (state !== prev && (state === 'connecting' || state === 'disconnected')) this.reset()
  }

  reset = () => { this.msg = null; this.arrived = null; this.cores = []; this.prev = []; this.publish() }

  version = (): number => this.ver
  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn)
    return () => { this.listeners.delete(fn) }
  }

  latest = (): UtilMsg | null => this.msg
  receivedAt = (): number | null => this.arrived

  // Points with ts > latest.ts - windowS, oldest first.
  cpuSeries = (core: number, windowS: number): UtilPoint[] => {
    if (!this.msg) return []
    const last = this.msg.ts
    return (this.cores[core] ?? []).filter((p) => p.ts > last - windowS)
  }
}

export const utilStore = new UtilStore()
