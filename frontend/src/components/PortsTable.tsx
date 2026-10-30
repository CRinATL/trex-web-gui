import { useId, useState } from 'react'
import { ApiError, type PortView } from '../api'
import { turnServiceOff } from '../captureGate'
import { portAria, portTitle, type PortLabel } from '../portLabels'
import { statsStore } from '../stats'
import { useStatsSelector } from '../useStats'
import { cell, humanBps, humanPps } from '../statsFormat'
import { trend, trendLevel } from '../trend'

type Fmt = (v: unknown) => string
const ROWS: { label: string; tx: string; rx: string; fmt: Fmt }[] = [
  { label: 'L1 bps', tx: 'tx_bps_L1', rx: 'rx_bps_L1', fmt: humanBps },
  { label: 'L2 bps', tx: 'tx_bps', rx: 'rx_bps', fmt: humanBps },
  { label: 'pps', tx: 'tx_pps', rx: 'rx_pps', fmt: humanPps },
]
const PACKET_ROWS: typeof ROWS = [{ label: 'packets', tx: 'opackets', rx: 'ipackets', fmt: cell }]
// Folded behind the Show bytes button, between packets and errors: the byte counts are rarely read.
const BYTES_ROWS: typeof ROWS = [{ label: 'bytes', tx: 'obytes', rx: 'ibytes', fmt: cell }]
const ERROR_ROWS: typeof ROWS = [{ label: 'errors', tx: 'oerrors', rx: 'ierrors', fmt: cell }]

// -3..3 over the newest five samples of one rate. The window is six periods, so five samples fit even when the
// stats tick runs late; the samples' own ts decide, never an assumed 1 s.
function useTrendLevel(scope: string, k: string): number {
  return useStatsSelector(() => {
    const interval = statsStore.latest()?.interval_s ?? 1
    const samples = statsStore.series(scope, 6 * interval).map((p) => p[k]).filter((x): x is number => typeof x === 'number')
    return trendLevel(trend(samples))
  })
}

function Trend({ scope, k }: { scope: string; k: string }) {
  const level = useTrendLevel(scope, k)
  if (level === 0) return null
  const dir = level > 0 ? 'rising' : 'falling'
  return <span role="img" aria-label={dir} title={`${dir[0].toUpperCase()}${dir.slice(1)}: ${TREND_HINT}`} className="trend">{(level > 0 ? '▲' : '▼').repeat(Math.abs(level))}</span>
}

function Val({ scope, k, fmt, arrow }: { scope: string; k: string; fmt: Fmt; arrow?: boolean }) {
  // relative() leaves rates and gauges raw and subtracts the Clear baseline from COUNTER_KEYS only.
  const v = useStatsSelector(() => statsStore.relative(scope, k))
  return <td>{fmt(v)}{arrow && <Trend scope={scope} k={k} />}</td>
}

const NO_LABEL = () => null
// Where service mode changes for a port this session does not own: the banner never acquires one.
export const SERVICE_HOW = 'Turn it off on the Control page: acquire the port, then its Control tab, Service mode Off.'
const plural = (ids: number[]) => `port${ids.length > 1 ? 's' : ''} ${ids.join(', ')}`
// The arrows port the TUI's trend (trend.ts); the tooltip says what they count.
const TREND_HINT = 'change over the last 5 samples: one arrow above 1 %, two above 2 %, three above 5 %'

// Service mode is a port's state, not the table's: the Dashboard shows this above its tabs, so every tab carries it
// (RC1-21). The state leads, in bold; how to change it follows. `canAct` (connected, not read-only): the ports this session
// owns get a button that turns it off (RC1-22), one port at a time as turnServiceOn does; the others keep the way by hand,
// since taking a port could take it from another user.
export function ServiceModeBanner({ ports, canAct = false }: { ports: PortView[]; canAct?: boolean }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const service = ports.filter((p) => p.service || p.service_filtered)
  if (service.length === 0) return null
  const mine = canAct ? service.filter((p) => p.owned).map((p) => p.id) : []
  const others = service.filter((p) => !mine.includes(p.id)).map((p) => p.id)
  async function turnOff() {
    setBusy(true); setError(null)
    try { await turnServiceOff(mine) }
    catch (x) { setError(x instanceof ApiError ? x.message : String(x)) }
    finally { setBusy(false) }
  }
  return (
    <p role="status" aria-label="Service mode" className="banner alert">
      <strong>Service mode is on for {plural(service.map((p) => p.id))}.</strong>{' '}
      {mine.length > 0 && <button type="button" data-run="go" disabled={busy} onClick={() => void turnOff()}>Turn service mode off for {plural(mine)}</button>}
      {others.length > 0 && (mine.length > 0 ? <> For {plural(others)}, acquire the port on the Control page, then its Control tab, Service mode Off.</> : SERVICE_HOW)}
      {error && <> <span role="alert" className="error">{error}</span></>}
    </p>
  )
}

export function PortsTable({ ports, labelOf = NO_LABEL }: { ports: PortView[]; labelOf?: (p: PortView) => PortLabel | null }) {
  const [show, setShow] = useState<'all' | 'my'>('all')
  const [bytes, setBytes] = useState(false)
  const bytesId = useId()
  const shown = show === 'my' ? ports.filter((p) => p.owned) : ports
  const scopes = [...shown.map((p) => String(p.id)), 'total']
  const body = (rows: typeof ROWS, arrow = false) =>
    rows.flatMap((r) => [['Tx', r.tx], ['Rx', r.rx]].map(([dir, k]) => (
      <tr key={k}>
        <th scope="row">{`${dir} ${r.label}`}</th>
        {scopes.map((sc) => <Val key={sc} scope={sc} k={k} fmt={r.fmt} arrow={arrow} />)}
      </tr>
    )))
  return (
    <div>
      <label>Show <select aria-label="Show ports" value={show} onChange={(e) => setShow(e.target.value as 'all' | 'my')}>
        <option value="all">All</option>
        <option value="my">My</option>
      </select></label>
      <button type="button" aria-expanded={bytes} aria-controls={bytesId} onClick={() => setBytes(!bytes)}>
        <span aria-hidden="true">{bytes ? '▾' : '▸'} </span>{bytes ? 'Hide bytes' : 'Show bytes'}
      </button>
      <table className="dash-table">
        <thead>
          <tr><th scope="col">Metric</th>{scopes.map((sc) => {
            const port = ports.find((p) => String(p.id) === sc)
            if (!port) return <th key={sc} scope="col">{show === 'my' ? 'Total (all ports)' : 'Total'}</th>
            const lab = labelOf(port)
            return <th key={sc} scope="col" aria-label={portAria(port, lab)} title={lab?.description || undefined}>{portTitle(port, lab)}</th>
          })}</tr>
        </thead>
        <tbody>
          {body(ROWS, true)}
          <tr><th colSpan={scopes.length + 1} scope="colgroup">Counters: since connect, Reset or Clear</th></tr>
          {body(PACKET_ROWS)}
        </tbody>
        <tbody id={bytesId} hidden={!bytes}>{body(BYTES_ROWS)}</tbody>
        <tbody>{body(ERROR_ROWS)}</tbody>
      </table>
    </div>
  )
}
