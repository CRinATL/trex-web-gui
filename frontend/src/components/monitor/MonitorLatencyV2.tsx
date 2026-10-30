import { useMemo } from 'react'
import { useMonitor } from '../../monitorStore'
import { fromKeyVal, topBuckets } from '../../latencyBuckets'
import { cell } from '../../statsFormat'
import { HistogramSvg } from '../HistogramSvg'

const rec = (v: unknown): Record<string, unknown> => (typeof v === 'object' && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {})
const SUMMARY = ['cnt', 'high_cnt', 'min_usec', 'max_usec', 's_avg', 's_max']

function Counters({ label, data, keys }: { label: string; data: Record<string, unknown>; keys: string[] }) {
  return (
    <dl aria-label={label}>
      {keys.map((k) => <div key={k}><dt>{k}</dt><dd>{cell(typeof data[k] === 'number' ? (data[k] as number) : null)}</dd></div>)}
    </dl>
  )
}

// The Latency v2 panel: the latency CPU, then one block for every `port-N` the publisher sends (not only the first pair).
export function MonitorLatencyV2() {
  const v2 = useMonitor((m) => m?.latency_v2 ?? null)
  const ports = useMemo(() => Object.keys(v2 ?? {}).flatMap((k) => { const m = /^port-(\d+)$/.exec(k); return m ? [{ key: k, n: Number(m[1]) }] : [] }).sort((a, b) => a.n - b.n), [v2])
  return (
    <div className="monitor-latency-v2">
      <p>CPU: {typeof v2?.cpu_util === 'number' ? `${cell(v2.cpu_util)} %` : '–'}</p>
      {ports.map(({ key, n }) => {
        const block = rec(v2?.[key]), hist = rec(block.hist), stats = rec(block.stats)
        return (
          <section key={key} aria-label={`Latency v2 port ${n}`}>
            <h3>Port {n}</h3>
            <HistogramSvg bars={topBuckets(fromKeyVal(hist.histogram), 'All')} scale="linear" label={`Latency histogram of port ${n}, packets per latency range`} />
            <Counters label={`Port ${n} latency summary`} data={hist} keys={SUMMARY} />
            <Counters label={`Port ${n} counters`} data={stats} keys={Object.keys(stats)} />
          </section>
        )
      })}
    </div>
  )
}
