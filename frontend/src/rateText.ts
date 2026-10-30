import { MULT_RE, type MultUnit } from './traffic'

export type RateKind = 'pps' | 'bps_L1' | 'bps_L2' | 'percentage' | 'factor'
export type ParsedRate = { kind: RateKind; value: number }

const RATE_RE = /^\s*(×|x)?\s*(\d+(?:\.\d*)?|\.\d+)\s*(×|x)?\s*([kmg])?\s*(pps|bps(?:\s*l1)?|%)?\s*$/i
const SCALES: Record<string, number> = { k: 1e3, m: 1e6, g: 1e9 }

const COPY_STUDIO = (t: string) => `Can't read "${t}": a number, optionally with k/M/G and pps, bps or %.`
const COPY_CONTROL = (t: string) => `Can't read "${t}": a number, optionally with k/M/G and pps, bps or %, or × for a factor.`

// The largest of G/M/k whose mantissa is at least 1; none below 1000. Shared by the box text and the wire text.
function scaled(value: number): { mantissa: string; prefix: 'G' | 'M' | 'k' | '' } {
  for (const [prefix, scale] of [['G', 1e9], ['M', 1e6], ['k', 1e3]] as const) {
    if (value >= scale) return { mantissa: String(value / scale), prefix }
  }
  return { mantissa: String(value), prefix: '' }
}

export function parseRate(text: string, bare: 'pps' | 'factor'): ParsedRate | { error: string } {
  const copy = bare === 'pps' ? COPY_STUDIO : COPY_CONTROL
  const trimmed = text.trim()
  if (trimmed === '' || /^-\s*[\d.]/.test(trimmed)) return { error: 'Enter a rate above 0.' }
  const m = RATE_RE.exec(text)
  if (!m) return { error: copy(text) }
  const [, pre, num, post, scaleCh, unitRaw] = m
  const unit = unitRaw?.toLowerCase().replace(/\s+/g, '')
  const marked = Boolean(pre || post)
  if (scaleCh && !unit) return { error: copy(text) }             // R2: a scale needs a unit
  if (unit === '%' && (scaleCh || marked)) return { error: copy(text) }
  if (marked && (unit || scaleCh || bare === 'pps' || (pre && post))) return { error: copy(text) }
  let value = Number(num)
  if (scaleCh) value = Number((value * SCALES[scaleCh.toLowerCase()]).toPrecision(15))
  if (!(value > 0)) return { error: 'Enter a rate above 0.' }
  let kind: RateKind
  if (marked) kind = 'factor'
  else if (unit === 'pps') kind = 'pps'
  else if (unit === 'bps') kind = 'bps_L2'
  else if (unit === 'bpsl1') kind = 'bps_L1'
  else if (unit === '%') kind = 'percentage'
  else kind = bare
  if (kind === 'percentage' && value > 100) return { error: 'A percentage is at most 100.' }
  return { kind, value }
}

export function formatRate(r: ParsedRate): string {
  if (r.kind === 'percentage') return `${r.value}%`
  if (r.kind === 'factor') return `×${r.value}`
  const { mantissa, prefix } = scaled(r.value)
  const unit = r.kind === 'pps' ? 'pps' : r.kind === 'bps_L1' ? 'bps L1' : 'bps'
  return `${mantissa} ${prefix}${unit}`
}

export function readingOf(r: ParsedRate): string {
  const n = (v: number) => v.toLocaleString('en-US', { maximumFractionDigits: 6 })
  switch (r.kind) {
    case 'pps': return `= ${n(r.value)} pps`
    case 'percentage': return `= ${n(r.value)} % of line rate`
    case 'factor': return `= ×${n(r.value)} the profile's rates`
    default: {
      const { mantissa, prefix } = scaled(r.value)
      return `= ${n(Number(mantissa))} ${prefix}bit/s (${r.kind === 'bps_L1' ? 'L1' : 'L2'})`
    }
  }
}

export function toWireMult(r: ParsedRate): string | null {
  let text: string
  if (r.kind === 'percentage') text = `${r.value}%`
  else if (r.kind === 'factor') text = String(r.value)
  else {
    const { mantissa, prefix } = scaled(r.value)
    text = `${mantissa}${prefix.toLowerCase()}${r.kind === 'pps' ? 'pps' : r.kind === 'bps_L1' ? 'bpsl1' : 'bps'}`
  }
  return MULT_RE.test(text) ? text : null
}

export function fromWireMult(m: string): ParsedRate | null {
  const t = m.trim().toLowerCase()
  if (!MULT_RE.test(t)) return null
  const x = /^(\d+(?:\.\d+)?)(?:([kmg]?)(bpsl1|pps|bps)|(%))?$/.exec(t)
  if (!x) return null
  const num = Number(x[1])
  if (x[4]) return { kind: 'percentage', value: num }
  if (!x[3]) return { kind: 'factor', value: num }
  const value = Number((num * (SCALES[x[2]] ?? 1)).toPrecision(15))
  return { kind: x[3] === 'pps' ? 'pps' : x[3] === 'bps' ? 'bps_L2' : 'bps_L1', value }
}

// The wire multiplier of a form's (multValue, unit) pair, exactly as the box reads it and as Enter sends it: one function for
// Start, a saved setup and its drift line. null when the pair is no multiplier above 0 that TRex reads (FormError, "not valid").
export function formMult(value: string, unit: MultUnit): string | null {
  const r = fromWireMult(`${value.trim()}${unit === 'x' ? '' : unit}`)
  return r && r.value > 0 ? toWireMult(r) : null
}
