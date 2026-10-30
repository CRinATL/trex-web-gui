import { useMemo, useState } from 'react'
import { useMonitor } from '../../monitorStore'
import { BAR_CHOICES, fromKeyVal, HIST_BARS, topBuckets, type BarChoice } from '../../latencyBuckets'
import { cell } from '../../statsFormat'
import { HistogramSvg, type HistogramScale } from '../HistogramSvg'

// The Rx-check panel: the counters of the rx-check engine (`rx-check.data.stats`), then its latency histogram.
export function MonitorRxCheck() {
  const rx = useMonitor((m) => m?.rx_check ?? null)
  const [scale, setScale] = useState<HistogramScale>('linear')
  const [count, setCount] = useState<BarChoice>(HIST_BARS)
  const stats = useMemo(() => {
    const s = rx?.stats
    return typeof s === 'object' && s !== null ? Object.entries(s).filter(([, v]) => typeof v === 'number') as [string, number][] : []
  }, [rx])
  const hist = useMemo(() => {
    const h = rx?.latency_hist
    return topBuckets(fromKeyVal(typeof h === 'object' && h !== null ? (h as { histogram?: unknown }).histogram : undefined), count)
  }, [rx, count])
  return (
    <div className="monitor-rxcheck">
      <dl aria-label="Rx-check counters">
        {stats.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{cell(v)}</dd></div>)}
      </dl>
      <label>
        Scale
        <select aria-label="Scale" value={scale} onChange={(e) => setScale(e.target.value === 'log' ? 'log' : 'linear')}>
          <option value="linear">Linear</option><option value="log">Log</option>
        </select>
      </label>
      <label>
        Bars
        <select aria-label="Bars" value={String(count)} onChange={(e) => setCount(BAR_CHOICES.find((x) => String(x) === e.target.value) ?? HIST_BARS)}>
          {BAR_CHOICES.map((c) => <option key={c} value={String(c)}>{c}</option>)}
        </select>
      </label>
      <HistogramSvg bars={hist} scale={scale} label="Rx-check latency histogram, packets per latency range" />
    </div>
  )
}
