import { useMemo } from 'react'
import { monitorStore, useMonitor } from '../../monitorStore'
import { stfLatencyRows } from '../../monitorLayouts'
import { cell } from '../../statsFormat'
import { TimeChart } from '../TimeChart'

const us = (v: number | null) => (cell(v) === '–' ? '–' : `${cell(v)} µs`)

// The Latency panel: the per-port table (µs), then the client-versus-server maximum over the last 16 s.
export function MonitorLatency() {
  const lat = useMonitor((m) => m?.latency ?? null)
  const rows = useMemo(() => stfLatencyRows(lat), [lat])
  return (
    <div className="monitor-latency">
      <table className="streams" aria-label="Latency per port">
        <thead><tr><th>Port</th><th>Side</th><th>Avg</th><th>Max</th><th>Max (period)</th><th>Errors</th><th>Jitter</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.port}>
              <th scope="row">{r.port}</th><td>{r.side}</td><td>{us(r.avg)}</td><td>{us(r.max)}</td><td>{us(r.cmax)}</td><td>{cell(r.error)}</td><td>{us(r.jitter)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <TimeChart legend="chips" title="Max latency, client vs server (16 s)" subscribe={monitorStore.subscribe} windowS={16} format={us}
        series={[{ label: 'Client', color: 'var(--series-1)' }, { label: 'Server', color: 'var(--series-2)' }]}
        latestTs={() => monitorStore.latencyWindow().ts.at(-1)}
        data={() => { const w = monitorStore.latencyWindow(); return [w.ts, w.client, w.server] }} />
    </div>
  )
}
