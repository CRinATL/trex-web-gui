import type { XstatsView } from './api'

type PortData = { values: Record<string, number>; baseline: Record<string, number>; pins: string[] }

// The browser owns the xstats baseline: the server reports absolute values and "Reset counters" only moves the
// baseline here (as legacy's shadow map did). Kept across navigation; dropped on a connect_seq change (state.tsx).
class XstatsStore {
  private ports = new Map<number, PortData>()
  private ver = 0
  private gen = 0
  private listeners = new Set<() => void>()

  private publish() { this.ver++; this.listeners.forEach((fn) => fn()) }
  private data(port: number): PortData {
    let d = this.ports.get(port)
    if (!d) { d = { values: {}, baseline: {}, pins: [] }; this.ports.set(port, d) }
    return d
  }

  push = (v: XstatsView) => { this.data(v.port).values = v.values; this.publish() }
  reset = (port: number) => { const d = this.data(port); d.baseline = { ...d.values }; this.publish() }
  clear = () => { this.ports.clear(); this.gen++; this.publish() }
  pin = (port: number, name: string) => {
    const d = this.data(port)
    if (!d.pins.includes(name)) { d.pins = [...d.pins, name]; this.publish() }
  }
  unpin = (port: number, name: string) => { const d = this.data(port); d.pins = d.pins.filter((n) => n !== name); this.publish() }

  relative = (port: number, name: string): number => {
    const d = this.ports.get(port)
    return d ? (d.values[name] ?? 0) - (d.baseline[name] ?? 0) : 0
  }
  pins = (port: number): string[] => this.ports.get(port)?.pins ?? []
  names = (port: number): string[] => Object.keys(this.ports.get(port)?.values ?? {})

  // Incremented by clear(): a request remembers it at send time and drops its answer if it changed.
  epoch = (): number => this.gen
  version = (): number => this.ver
  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn)
    return () => { this.listeners.delete(fn) }
  }
}

export const xstatsStore = new XstatsStore()
