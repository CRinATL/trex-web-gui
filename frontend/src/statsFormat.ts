// Port counters, and nothing else. queue_full is shown raw and is not baselined.
export const COUNTER_KEYS = ['opackets', 'ipackets', 'obytes', 'ibytes', 'oerrors', 'ierrors'] as const

const DASH = '–'

// A usable reading is a finite, non-negative number; null, 'N/A', NaN, inf and negatives are not.
const usable = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v) && v >= 0

function scaled(v: unknown, unit: string): string {
  if (!usable(v)) return DASH
  const prefixes = ['', 'k', 'M', 'G', 'T']
  let i = 0
  let x = v
  // The unit comes from the rounded value, so 999.995 reads "1.00 kbps", not "1000 bps".
  const shown = () => (i === 0 && x >= 10 ? x.toFixed(0) : x.toFixed(2))
  while (i < prefixes.length - 1 && (x >= 1000 || Number(shown()) >= 1000)) { x /= 1000; i++ }
  return i === 0 ? `${Number(shown())} ${unit}` : `${shown()} ${prefixes[i]}${unit}`
}

export const humanBps = (v: unknown): string => scaled(v, 'bps')
export const humanPps = (v: unknown): string => scaled(v, 'pps')

// 20 bytes of preamble and inter-packet gap per packet, times 8 bits (inventory A8.1).
export const l1Bps = (bps: number | null, pps: number | null): number | null =>
  bps === null || pps === null ? null : bps + pps * 20 * 8

// A plain count or reading; anything unusable is an en dash, never "NaN".
export const cell = (v: unknown): string => (usable(v) ? v.toLocaleString('en-US', { maximumFractionDigits: 2 }) : DASH)
