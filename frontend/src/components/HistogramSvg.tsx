import { bucketRange } from '../latencyBuckets'
import { cell } from '../statsFormat'

const ROW = 22, LABEL_W = 130, BAR_W = 260, COUNT_W = 90

export type HistogramScale = 'linear' | 'log'

const rangeLabel = (key: number): string => {
  const { lo, hi } = bucketRange(key)
  return hi === null ? `≥ ${cell(lo)} µs` : `${cell(lo)}–${cell(hi)} µs`
}

// A bar's length: linear sizes it by count; log sizes it by log10(1 + count), so a small bucket stays visible beside a huge one.
const size = (count: number, scale: HistogramScale) => (scale === 'log' ? Math.log10(1 + count) : count)

// Latency buckets as rows of an SVG, each with its range and count as text. `label` names the figure.
export function HistogramSvg({ bars, label, scale }: { bars: { key: number; count: number }[]; label: string; scale: HistogramScale }) {
  if (bars.length === 0) return <p>No samples yet</p>
  const top = Math.max(Number.EPSILON, ...bars.map((b) => size(b.count, scale)))
  return (
    <svg role="list" aria-label={label} width={LABEL_W + BAR_W + COUNT_W} height={bars.length * ROW}>
      {bars.map((b, i) => (
        <g key={b.key} role="listitem" aria-label={`${rangeLabel(b.key)}: ${cell(b.count)} packets`} transform={`translate(0 ${i * ROW})`} data-testid="hist-bar">
          <text x={LABEL_W - 6} y={ROW - 7} textAnchor="end" fill="currentColor" fontSize="12">{rangeLabel(b.key)}</text>
          <rect aria-hidden="true" x={LABEL_W} y={3} width={Math.max(1, (size(b.count, scale) / top) * BAR_W)} height={ROW - 6} fill="var(--accent, #3b5bdb)" />
          <text x={LABEL_W + BAR_W + 6} y={ROW - 7} fill="currentColor" fontSize="12">{cell(b.count)}</text>
        </g>
      ))}
    </svg>
  )
}
