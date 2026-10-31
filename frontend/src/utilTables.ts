import type { UtilMsg } from './api'
import { PGID_PALETTE } from './pgidPalette'

export type Core = UtilMsg['cpu'][number]

const num = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

export const CPU_WINDOW_S = 300
export const CHART_CORES = PGID_PALETTE.length   // one colour each

// Python's round(): half to even. The server's means are n/5 or n/len, so a .5 happens for histories of 2 or 4 samples.
export function roundHalfEven(x: number): number {
  const f = Math.floor(x)
  const d = x - f
  return d < 0.5 ? f : d > 0.5 ? f + 1 : f % 2 === 0 ? f : f + 1
}

// to_table_cpu's Avg: int(round(sum(history[:avg_len]) / avg_len)), avg_len = min(5, len). null when any sample is missing.
// Each core's own history length is used here; the TUI uses core 0's length for every core (the server always sends 20, so they agree).
export function cpuAvg(history: (number | null)[]): number | null {
  const n = Math.min(5, history.length)
  if (n === 0) return null
  const head = history.slice(0, n)
  return head.every(num) ? roundHalfEven(head.reduce((a, b) => a + b, 0) / n) : null
}

// The ports a core serves, as text: "idle" (both -1), "0" or "0, 1".
export function portsText(c: Core): string {
  const used = c.ports.filter((p) => p !== -1)
  return used.length === 0 ? 'idle' : used.join(', ')
}

// The chart's name for a core: Core N (ports a, b), Core N (port a), Core N (idle).
export function coreLabel(i: number, c: Core): string {
  const used = c.ports.filter((p) => p !== -1)
  return `Core ${i} (${used.length === 0 ? 'idle' : used.length === 1 ? `port ${used[0]}` : `ports ${used.join(', ')}`})`
}

// UtilStats.get_number_of_bytes: '64b' is 64, '9kb' is 9000.
export const mbufBytes = (size: string): number => parseInt(size.slice(0, -1).replace('k', '000'), 10)

// 'cpu-socket-0' as to_table_mbuf names it: 'Socket 0'.
export function socketName(key: string): string {
  const s = key.replace(/cpu-/g, '').replace(/-/g, ' ')
  return s.charAt(0).toUpperCase() + s.slice(1).toLowerCase()
}

