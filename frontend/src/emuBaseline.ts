import { macText, type EmuCounterRow, type EmuNsKey, type EmuScope } from './api'
import { nsId } from './emuFormat'

export const EMU_BASELINE_KEY = 'trex-web-gui.emuBaseline'
const KEEP = 50

// What "Clear" remembers for one counters scope: the time, and every counter's value then, keyed `<table>/<name>`.
export interface EmuBaseline { at: number; values: Record<string, number> }
export type EmuBaselines = Record<string, EmuBaseline>

// The identity of a scope's counters: the global scope, a namespace, or one client of a namespace.
export function baselineKey(scope: EmuScope, ns: EmuNsKey | null, mac: readonly number[] | null): string {
  if (scope === 'ctx' || !ns) return 'ctx'
  return scope === 'ns' || !mac ? `ns:${nsId(ns)}` : `client:${nsId(ns)}:${macText(mac)}`
}
// A counter's place in a baseline.
export const counterId = (table: string, row: EmuCounterRow) => `${table}/${row.name}`

// Every baseline this browser stored; anything unreadable or malformed in storage is dropped.
export function readBaselines(): EmuBaselines {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(EMU_BASELINE_KEY) ?? '{}')
    if (typeof v !== 'object' || v === null || Array.isArray(v)) return {}
    const out: EmuBaselines = {}
    for (const [k, b] of Object.entries(v)) {
      const e = b as Partial<EmuBaseline> | null
      if (e && typeof e.at === 'number' && typeof e.values === 'object' && e.values !== null) out[k] = { at: e.at, values: e.values }
    }
    return out
  } catch { return {} }
}
// Stores `b` under `key` (the newest KEEP entries are kept) and returns the map; a browser that refuses storage keeps it for this call only.
export function storeBaseline(key: string, b: EmuBaseline): EmuBaselines {
  const next = { ...readBaselines(), [key]: b }
  const keep = Object.entries(next).sort((x, y) => y[1].at - x[1].at).slice(0, KEEP)
  const out = Object.fromEntries(keep)
  try { localStorage.setItem(EMU_BASELINE_KEY, JSON.stringify(out)) } catch { /* storage unavailable: the baseline lives in the page only */ }
  return out
}
// The baseline of the counters in `tables` now (a null value counts as absent).
export function takeBaseline(tables: Record<string, EmuCounterRow[]>, at: number): EmuBaseline {
  const values: Record<string, number> = {}
  for (const [t, rows] of Object.entries(tables)) for (const r of rows) if (r.value !== null) values[counterId(t, r)] = r.value
  return { at, values }
}
// A counter's value less its baseline; one the baseline lacks shows its total, and a null value stays null.
export function deltaOf(value: number | null, base: number | undefined): number | null {
  return value === null ? null : base === undefined ? value : value - base
}
// Forgets every stored baseline (a new connection to the server: its counters start again, so an old baseline would give negative deltas).
export function clearBaselines(): void {
  try { localStorage.removeItem(EMU_BASELINE_KEY) } catch { /* storage unavailable: nothing was stored */ }
}
