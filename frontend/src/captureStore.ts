import type { CapturePktsMsg, DecodedRow } from './api'

export const CAPACITY = 5000

// The monitor's decoded rows, newest last. Browser-side and outside the reducer (the statsStore pattern): a batch arrives
// every 0.5 s and must not re-render the whole tree. Oldest rows go first and are counted, so the page can say how many
// packets it no longer holds. The ring holds one monitor's rows: a batch from another monitor (a new one, started in this
// tab or any other) empties it first, so every tab follows the new monitor and never mixes two captures.
class CaptureStore {
  private held: DecodedRow[] = []
  private dropped = 0
  private monitorId: number | null = null
  private listeners = new Set<() => void>()

  private publish() { this.listeners.forEach((fn) => fn()) }

  push = (msg: CapturePktsMsg) => {
    if (this.monitorId !== null && msg.monitor_id !== this.monitorId) this.clear()
    this.monitorId = msg.monitor_id
    if (!msg.rows.length) return
    const all = this.held.concat(msg.rows)
    const over = Math.max(0, all.length - CAPACITY)
    this.dropped += over
    this.held = over ? all.slice(over) : all     // a new array each time: rows() identity changes only here
    this.publish()
  }

  // Same array until the next push or clear, so useSyncExternalStore does not loop.
  rows = (): DecodedRow[] => this.held
  droppedByBrowser = (): number => this.dropped

  clear = () => {
    this.held = []
    this.dropped = 0
    this.monitorId = null
    this.publish()
  }

  // A connect_seq change (state.tsx): nothing of the old connection's capture carries over.
  reset = () => this.clear()

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn)
    return () => { this.listeners.delete(fn) }
  }
}

export const captureStore = new CaptureStore()
