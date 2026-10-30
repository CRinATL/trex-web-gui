import { useMemo, useState, useSyncExternalStore } from 'react'
import { DEFAULT_INTERVAL } from '../chartSeries'
import { CAP_REASON, capReasonId } from '../pgidPalette'
import { pgidStore, WINDOW_LEN } from '../pgidStats'
import { cell } from '../statsFormat'
import { LatencyHistogram } from './LatencyHistogram'
import { pgidSource, useStale } from '../useStale'
import { PgidSelector, type Selection } from './PgidSelector'
import { ChartCheckbox, PgidChart, type Metric } from './PgidChart'

const MAX_NOTE = 'the largest last max seen since connect or Clear'
const SERVER_MAX_NOTE = 'the server\'s maximum since the stream was added; Clear does not reset it'
const LAST_NOTE = 'the server\'s maximum over its latest sample period'

const num = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

// Errors as tui sums them (trex_stl_stats.py, the latency table's Errors): relative seq_too_low + seq_too_high only.
// The other three counters derive from the same events. null when the server sent neither.
function errors(id: number): number | null {
  const lo = pgidStore.relativeErr(id, 'seq_too_low')
  const hi = pgidStore.relativeErr(id, 'seq_too_high')
  return lo === null && hi === null ? null : (lo ?? 0) + (hi ?? 0)
}

function Table({ ids, sel }: { ids: number[]; sel: Selection }) {
  const lastCols = Array.from({ length: WINDOW_LEN }, (_, i) => i)
  return (
    <table className="dash-table" aria-label="Latency">
      <thead>
        <tr>
          <th scope="col">Chart</th><th scope="col">PG ID</th>
          <th scope="col" title={MAX_NOTE}>Max (µs)</th>
          <th scope="col" title={SERVER_MAX_NOTE}>Server max (µs)</th>
          <th scope="col">Avg (µs)</th>
          {lastCols.map((i) => <th key={i} scope="col" title={LAST_NOTE}>{i === 0 ? 'Last (µs)' : `Last-${i} (µs)`}</th>)}
          <th scope="col">Jitter (µs)</th>
          <th scope="col" title="sequence too low plus sequence too high, since Clear">Errors</th>
        </tr>
      </thead>
      <tbody>
        {ids.map((id) => {
          const lat = pgidStore.latest()?.latency[String(id)]?.latency
          const win = pgidStore.window(id)
          return (
            <tr key={id}>
              <td><ChartCheckbox tab="latency" id={id} sel={sel} /></td>
              <th scope="row">{id}</th>
              <td>{cell(pgidStore.runningMax(id))}</td>
              <td>{cell(lat?.total_max)}</td>
              <td>{cell(lat?.average)}</td>
              {lastCols.map((i) => <td key={i}>{cell(win[i])}</td>)}
              <td>{cell(lat?.jitter)}</td>
              <td>{cell(errors(id))}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

const micros = (v: number | null) => (num(v) ? `${v.toLocaleString('en-US', { maximumFractionDigits: 2 })} µs` : '–')
const METRICS: Metric[] = [
  { id: 'lat_avg', label: 'Average', format: micros }, { id: 'lat_max', label: 'Max', format: micros },
  { id: 'lat_last_max', label: 'Last max', format: micros }, { id: 'lat_jitter', label: 'Jitter', format: micros },
]

// The Latency tab: per-pg_id table, the chart selection, the histogram and the chart.
// visible: false while another dashboard tab is shown. The table and histogram are then not rendered; the selector and chart
// stay mounted so the chart is not redrawn.
export function LatencyTab({ visible = true }: { visible?: boolean }) {
  const version = useSyncExternalStore(pgidStore.subscribe, pgidStore.version)
  const ids = useMemo(() => pgidStore.latencyIds(), [version])   // eslint-disable-line react-hooks/exhaustive-deps
  const [windowS, setWindowS] = useState<number>(DEFAULT_INTERVAL)
  const [histId, setHistId] = useState<number | null>(null)
  // Stale before any message: the read has failed since connect, so "no streams" would be a claim nobody measured.
  const unheard = useStale(pgidSource) && pgidStore.latest() === null
  return (
    <PgidSelector tab="latency" ids={ids}>
      {(sel) => (
        <>
          {sel.atCap && <p id={capReasonId('latency')}>{CAP_REASON}</p>}
          <div className="overview-grid">
            <div>
              {visible && (ids.length === 0 ? (unheard ? null : <p>No streams with latency are active.</p>) : <Table ids={ids} sel={sel} />)}
              {visible && ids.length > 0 && <LatencyHistogram ids={ids} chosen={histId} onChoose={setHistId} />}
            </div>
            <div>
              <PgidChart title={(m) => `Latency: ${m.label} (µs)`} metrics={METRICS} metricLabel="Latency chart metric" intervalLabel="Latency interval" sel={sel} windowS={windowS} setWindowS={setWindowS} />
            </div>
          </div>
        </>
      )}
    </PgidSelector>
  )
}
