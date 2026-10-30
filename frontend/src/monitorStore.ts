import { useSyncExternalStore } from 'react'
import type { MonitorMsg, MonitorTarget } from './api'
import { clientServerMax } from './monitorLayouts'

export const CAPACITY = 300          // history points kept (spec: charts keep 300 points)
export const LATENCY_WINDOW_S = 16   // client vs server max latency (spec C6)

export interface MonitorHistory { ts: number[]; tx_bps: (number | null)[]; rx_bps: (number | null)[]; tx_pps: (number | null)[]; rx_pps: (number | null)[]; cpu: (number | null)[] }
export interface LatencyWindow { ts: number[]; client: (number | null)[]; server: (number | null)[] }
export interface MonitorSnapshot { latest: MonitorMsg | null; version: number }

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const sameTarget = (a: MonitorTarget, b: MonitorTarget) => a.host === b.host && a.port === b.port

const emptyHistory = (): MonitorHistory => ({ ts: [], tx_bps: [], rx_bps: [], tx_pps: [], rx_pps: [], cpu: [] })
const emptyWindow = (): LatencyWindow => ({ ts: [], client: [], server: [] })

// The newest `monitor` message plus the two series the charts need. Like statsStore, it lives outside the reducer
// (a message every 0.5 s), and getSnapshot hands back the same object until the next change.
class MonitorStore {
  private snap: MonitorSnapshot = { latest: null, version: 0 }
  private hist: MonitorHistory = emptyHistory()
  private lat: LatencyWindow = emptyWindow()
  private lastTs: number | null = null
  private lastFrames = 0
  private pulses = 0
  // The last non-null target seen: a disconnect (target null) and a reconnect to the same one keeps the history.
  private target: MonitorTarget | null = null
  private listeners = new Set<() => void>()

  private publish() {
    this.snap = { latest: this.snap.latest, version: this.snap.version + 1 }
    this.listeners.forEach((fn) => fn())
  }

  push = (m: MonitorMsg) => {
    if (m.target && this.target && !sameTarget(m.target, this.target)) this.reset()
    if (m.target) this.target = m.target
    if (m.frames > this.lastFrames) this.pulses++
    this.lastFrames = m.frames
    if (m.ts !== null && m.ts !== this.lastTs) {
      this.lastTs = m.ts
      this.appendHistory(m, m.ts)
    }
    this.snap = { latest: m, version: this.snap.version + 1 }
    this.listeners.forEach((fn) => fn())
  }

  private appendHistory(m: MonitorMsg, ts: number) {
    const g = m.global
    if (g) {
      const h = this.hist
      h.ts.push(ts); h.tx_bps.push(num(g.m_tx_bps)); h.rx_bps.push(num(g.m_rx_bps))
      h.tx_pps.push(num(g.m_tx_pps)); h.rx_pps.push(num(g.m_rx_pps)); h.cpu.push(num(g.m_cpu_util))
      if (h.ts.length > CAPACITY) {
        const cut = h.ts.length - CAPACITY
        for (const k of Object.keys(h) as (keyof MonitorHistory)[]) h[k].splice(0, cut)
      }
      this.hist = { ...h }   // a new object, so a reader that cached the previous one sees the change
    }
    if (m.latency) {
      const w = this.lat
      const side = clientServerMax(m.latency)
      w.ts.push(ts); w.client.push(side.client); w.server.push(side.server)
      let cut = 0
      while (cut < w.ts.length && !(w.ts[cut] > ts - LATENCY_WINDOW_S)) cut++
      if (cut) { w.ts.splice(0, cut); w.client.splice(0, cut); w.server.splice(0, cut) }
      this.lat = { ...w }
    }
  }

  private wipe() {
    this.hist = emptyHistory(); this.lat = emptyWindow()
    this.lastTs = null; this.lastFrames = 0
    this.snap = { latest: null, version: this.snap.version }
  }

  // Runs only when a monitor message names a different target; never on a Control reconnect.
  reset = () => { this.wipe(); this.target = null; this.publish() }

  get = (): MonitorMsg | null => this.snap.latest
  snapshot = (): MonitorSnapshot => this.snap
  history = (): MonitorHistory => this.hist
  latencyWindow = (): LatencyWindow => this.lat
  pulse = (): number => this.pulses
  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn)
    return () => { this.listeners.delete(fn) }
  }
}

export const monitorStore = new MonitorStore()

// The selector must return a primitive or a reference that stays the same until the message changes (a field of the
// message qualifies): a fresh object per call makes useSyncExternalStore loop.
export function useMonitor<T>(sel: (m: MonitorMsg | null) => T): T {
  return useSyncExternalStore(monitorStore.subscribe, () => sel(monitorStore.snapshot().latest))
}
// The pulse counter, for the LED: it re-renders on every message and reads the counter.
export const usePulse = (): number => useSyncExternalStore(monitorStore.subscribe, monitorStore.pulse)
