import type { Tunable } from './api'

// One list for the type and for parseMult; 'x' (no suffix) is the plain multiplier.
export const MULT_UNITS = ['x', '%', 'pps', 'kpps', 'mpps', 'gpps', 'bps', 'kbps', 'mbps', 'gbps', 'bpsl1', 'kbpsl1', 'mbpsl1', 'gbpsl1'] as const
export type MultUnit = (typeof MULT_UNITS)[number]

// Mirror chain: this mirrors MULT in backend/trex_web_gui/validation.py, which mirrors the multiplier regex at
// trex/utils/parsing_opts.py:88 (minus the update "+"/"-" suffix). The backend is the authority; this only
// refuses early what it would refuse anyway.
export const MULT_RE = /^\d+(\.\d+)?(([kmg]?(bpsl1|pps|bps))|%)?$/

// A wire multiplier back to the form's value and unit (the inverse of rateText's formMult). null for a text the form cannot show
// (no number, or a suffix MULT_UNITS does not list, such as "fast" or "mpps+").
export function parseMult(mult: string): { value: string; unit: MultUnit } | null {
  const m = /^(\d+(?:\.\d+)?)([a-z%0-9]*)$/.exec(mult.trim().toLowerCase())
  if (!m) return null
  if (m[2] === '') return { value: m[1], unit: 'x' }
  const unit = MULT_UNITS.find((u) => u !== 'x' && u === m[2])
  return unit ? { value: m[1], unit } : null
}

export function defaultsFor(specs: Tunable[]): Record<string, unknown> {
  return Object.fromEntries(specs.map((s) => [s.dest, s.default ?? (s.kind === 'bool_true' || s.kind === 'bool_false' ? false : '')]))
}

const words = (s: string) => s.trim().split(/\s+/).filter(Boolean)

// The tunables become the CLI-style argument list the profile's get_streams() parser receives. Values equal
// to their default are omitted, so an untouched form sends nothing.
export function tunablesToArgs(specs: Tunable[], values: Record<string, unknown>, extra: string): string[] {
  const out: string[] = []
  for (const s of specs) {
    const v = values[s.dest]
    const def = s.default ?? (s.kind === 'bool_true' || s.kind === 'bool_false' ? false : '')
    // form inputs hold strings, so compare as text: '64' from a number input equals a default of 64
    if (v === undefined || v === null || v === def || (typeof v !== 'boolean' && String(v) === String(def))) continue
    switch (s.kind) {
      case 'bool_true': if (v === true) out.push(s.flag); break
      case 'bool_false': if (v === false) out.push(s.flag); break
      case 'raw': {
        const toks = words(String(v))
        if (toks.length) out.push(...(s.flag ? [s.flag] : []), ...toks)
        break
      }
      default:
        if (String(v) === '') break   // a cleared field means "not set", not an empty argument
        out.push(s.flag, String(v))
    }
  }
  return [...out, ...words(extra)]
}
