const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const sig = (v: number) => String(Number(v.toPrecision(3)))

export function humanBps(v: number): string {
  const units = ['bps', 'kbps', 'Mbps', 'Gbps']
  let i = 0
  while (v >= 1000 && i < units.length - 1) { v /= 1000; i++ }
  return `${sig(v)} ${units[i]}`
}

/** One readable line from validate's `rate` (keys as recorded in tests/fixtures/p2/summary.json); null if absent.
 * max_line_util is already a percentage (1216 bps L1 on a 10 Gbps port is 1.2e-5). */
export function rateLine(info: Record<string, unknown>): string | null {
  const r = info.rate
  if (typeof r !== 'object' || r === null) return null
  const { max_pps: pps, max_bps_l2: bps, max_line_util: util } = r as Record<string, unknown>
  const parts: string[] = []
  if (num(pps) !== null) parts.push(`max ${sig(num(pps) as number)} pps`)
  if (num(bps) !== null) parts.push(`L2 ${humanBps(num(bps) as number)}`)
  if (num(util) !== null) parts.push(`${sig(num(util) as number)}% line utilisation`)
  return parts.length ? parts.join(', ') : null
}
