import { Fragment, useMemo, useState, useSyncExternalStore } from 'react'
import type { PortView } from '../api'
import { useDashView } from '../dashView'
import { MAX_PGIDS, pgidStore, type FlowCounter } from '../pgidStats'
import { portAria, portTitle, type PortLabel } from '../portLabels'
import { cell, humanBps, humanPps, l1Bps } from '../statsFormat'
import { TableViewControls } from './TableViewControls'
import { PgidSelector, type Selection } from './PgidSelector'
import { CAP_REASON, capReasonId } from '../pgidPalette'
import { pgidSource, useStale } from '../useStale'
import { ChartCheckbox, PgidChart, type Metric } from './PgidChart'

export const PAGE_SIZE = 100
const COUNTER_NOTE = 'since the stream was added, or Clear'
// The empty Streams tab says how a stream gets a PG ID and what it buys (RC1-39).
export const PGID_HOW = 'A stream gets one in Editor > Stream settings: set RX stats to Flow stats or Latency and give it a '
  + 'PG ID, then assign the profile and start the port. TRex then counts that stream on its own, sent and received back '
  + 'at a TRex port; Latency adds latency, jitter and sequence errors.'

const num = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

// One server rate for a scope ('total' or a port number); null when the server sent none.
function rate(id: number, field: string, scope: string): number | null {
  const v = pgidStore.latest()?.flow[String(id)]?.[field]?.[scope]
  return num(v) ? v : null
}

// Rx bps L1. The server sends rx_bps_l1 (recorded in __fixtures__/pgidMsg.json), so it is used as sent. Were a server to
// omit the field altogether, L1 is computed the way P3-min does for ports (statsFormat l1Bps): rx_bps plus rx_pps x 20 bytes x 8.
function rxBpsL1(id: number, scope: string): number | null {
  const f = pgidStore.latest()?.flow[String(id)]
  if (f && 'rx_bps_l1' in f) return rate(id, 'rx_bps_l1', scope)
  return l1Bps(rate(id, 'rx_bps', scope), rate(id, 'rx_pps', scope))
}

// The packet-rate cells shared by a pg_id's row (scope 'total') and its per-port rows.
function packetRateCells(id: number, scope: string) {
  return (
    <>
      <td className="num">{humanPps(rate(id, 'tx_pps', scope))}</td>
      <td className="num">{humanPps(rate(id, 'rx_pps', scope))}</td>
    </>
  )
}

// The bit-rate cells (Columns: Bit rates).
function bitRateCells(id: number, scope: string) {
  return (
    <>
      <td className="num">{humanBps(rate(id, 'tx_bps_l1', scope))}</td>
      <td className="num">{humanBps(rate(id, 'tx_bps', scope))}</td>
      <td className="num">{humanBps(rxBpsL1(id, scope))}</td>
      <td className="num">{humanBps(rate(id, 'rx_bps', scope))}</td>
    </>
  )
}

// The counter cells (Columns: Packet and byte counters).
function counterCells(id: number, scope: string) {
  const count = (f: FlowCounter) => cell(pgidStore.relative(id, f, scope))
  return (
    <>
      <td className="num">{count('tx_pkts')}</td>
      <td className="num">{count('rx_pkts')}</td>
      <td className="num">{count('tx_bytes')}</td>
      <td className="num">{count('rx_bytes')}</td>
    </>
  )
}

const METRICS: Metric[] = [
  { id: 'tx_pps', label: 'Tx pps', format: humanPps },
  { id: 'rx_pps', label: 'Rx pps', format: humanPps },
  { id: 'tx_bps_l1', label: 'Tx bps L1', format: humanBps },
  { id: 'tx_bps', label: 'Tx bps L2', format: humanBps },
  { id: 'rx_bps', label: 'Rx bps L2', format: humanBps },
  { id: 'loss_pps', label: 'Loss/s', format: humanPps },
]

// A port's heading text, its description (tooltip), and its accessible name. 'total' and unknown ports have no description.
function portHeading(k: string, ports: PortView[], labelOf: (p: PortView) => PortLabel | null) {
  if (k === 'total') return { text: 'Total', title: undefined, aria: 'Total' }
  const p = ports.find((x) => String(x.id) === k)
  if (!p) return { text: `Port ${k}`, title: undefined, aria: `Port ${k}` }
  const lab = labelOf(p)
  return { text: portTitle(p, lab), title: lab?.description || undefined, aria: portAria(p, lab) }
}

function PortHead({ k, ports, labelOf }: { k: string; ports: PortView[]; labelOf: (p: PortView) => PortLabel | null }) {
  const h = portHeading(k, ports, labelOf)
  return <th scope="row" colSpan={2} title={h.title} aria-label={h.aria}>{h.text}</th>
}

function Table({ ids, ports, labelOf, sel }: { ids: number[]; ports: PortView[]; labelOf: (p: PortView) => PortLabel | null; sel: Selection }) {
  const { view } = useDashView()
  const [page, setPage] = useState(0)
  const [open, setOpen] = useState<Set<number>>(new Set())
  const pages = Math.max(1, Math.ceil(ids.length / PAGE_SIZE))
  const cur = Math.min(page, pages - 1)
  const shown = ids.slice(cur * PAGE_SIZE, (cur + 1) * PAGE_SIZE)
  const latency = new Set(pgidStore.latest()?.active.latency ?? [])
  const toggleOpen = (id: number) => setOpen((o) => { const n = new Set(o); if (!n.delete(id)) n.add(id); return n })

  return (
    <>
      <TableViewControls tab="streams" />
      {ids.length > PAGE_SIZE && (
        <div className="pager">
          <label>
            Page
            <select aria-label="Page" value={cur} onChange={(e) => setPage(Number(e.target.value))}>
              {Array.from({ length: pages }, (_, i) => <option key={i} value={i}>{i + 1}</option>)}
            </select>
          </label>
          <span> of {pages} ({cur * PAGE_SIZE + 1}–{cur * PAGE_SIZE + shown.length} of {ids.length})</span>
        </div>
      )}
      <table className="dash-table" aria-label="Streams">
        <thead>
          <tr>
            <td colSpan={3} />
            <th colSpan={2} scope="colgroup">Packet rates</th>
            <th colSpan={2} scope="colgroup">Loss</th>
            {view.bitRates && <th colSpan={4} scope="colgroup">Bit rates</th>}
            {view.counters && <th colSpan={4} scope="colgroup">Counters {COUNTER_NOTE}</th>}
          </tr>
          <tr>
            <th scope="col">Chart</th><th scope="col">PG ID</th><th scope="col">Type</th>
            <th scope="col" className="num">Tx pps</th><th scope="col" className="num">Rx pps</th>
            <th scope="col" className="num" title={COUNTER_NOTE}>Loss</th><th scope="col" className="num">Loss/s</th>
            {view.bitRates && <><th scope="col" className="num">Tx bps L1</th><th scope="col" className="num">Tx bps L2</th><th scope="col" className="num">Rx bps L1</th><th scope="col" className="num">Rx bps L2</th></>}
            {view.counters && <>
              <th scope="col" className="num" title={COUNTER_NOTE}>Tx pkts</th><th scope="col" className="num" title={COUNTER_NOTE}>Rx pkts</th>
              <th scope="col" className="num" title={COUNTER_NOTE}>Tx bytes</th><th scope="col" className="num" title={COUNTER_NOTE}>Rx bytes</th>
            </>}
          </tr>
        </thead>
        <tbody>
          {shown.map((id) => {
            const f = pgidStore.latest()?.flow[String(id)]
            const portKeys = Object.keys(f?.tx_pkts ?? {}).filter((k) => k !== 'total')
            const expanded = open.has(id)
            return (
              <Fragment key={id}>
                <tr>
                  <td>
                    <ChartCheckbox tab="streams" id={id} sel={sel} />
                  </td>
                  <th scope="row">
                    <button type="button" aria-expanded={expanded} aria-label={`PG ID ${id} ports`} onClick={() => toggleOpen(id)}>{expanded ? '▾' : '▸'}</button>
                    {' '}{id}
                  </th>
                  <td>{latency.has(id) ? 'latency' : 'flow'}</td>
                  {packetRateCells(id, 'total')}
                  <td className="num">{cell(pgidStore.lossPkts(id))}</td>
                  <td className="num">{humanPps(pgidStore.lossPps(id))}</td>
                  {view.bitRates && bitRateCells(id, 'total')}
                  {view.counters && counterCells(id, 'total')}
                </tr>
                {expanded && portKeys.map((k) => (
                  <tr key={k} className="port-row">
                    <td />
                    <PortHead k={k} ports={ports} labelOf={labelOf} />
                    {packetRateCells(id, k)}
                    <td className="num" /><td className="num" />
                    {view.bitRates && bitRateCells(id, k)}
                    {view.counters && counterCells(id, k)}
                  </tr>
                ))}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </>
  )
}

function FlowErrors({ ports, labelOf }: { ports: PortView[]; labelOf: (p: PortView) => PortLabel | null }) {
  const g = pgidStore.latest()?.flow_global
  if (!g) return null
  const line = (name: 'rx_err' | 'tx_err') => {
    const keys = Object.keys(g[name] ?? {})
    if (keys.length === 0) return '–'
    return keys.map((k, i) => {
      const h = portHeading(k, ports, labelOf)
      return <span key={k} title={h.title}>{i > 0 && '; '}{h.text}: {cell(pgidStore.relativeFlowGlobal(name, k))}</span>
    })
  }
  return (
    <dl className="flow-errors" aria-label="Global flow errors">
      <dt>rx_err</dt><dd>{line('rx_err')}</dd>
      <dt>tx_err</dt><dd>{line('tx_err')}</dd>
    </dl>
  )
}

// The Streams tab: per-pg_id table, the chart selection, the chart, and the global flow errors.
// visible: false while another dashboard tab is shown. The table and errors are then not rendered; the selector and chart stay
// mounted so the chart is not redrawn.
export function StreamsTab({ ports, labelOf, visible = true }: { ports: PortView[]; labelOf: (p: PortView) => PortLabel | null; visible?: boolean }) {
  const version = useSyncExternalStore(pgidStore.subscribe, pgidStore.version)
  const all = useMemo(() => pgidStore.ids(), [version])   // eslint-disable-line react-hooks/exhaustive-deps
  const { view, setView } = useDashView()
  const msg = pgidStore.latest()
  // Stale before any message: the read has failed since connect, so "no streams" would be a claim nobody measured.
  const unheard = useStale(pgidSource) && msg === null
  const truncated = msg?.truncated ?? false
  // The backend queried the first MAX_PGIDS ids (ascending); the rest are listed as active but have no data.
  const ids = truncated ? all.slice(0, MAX_PGIDS) : all
  return (
    <PgidSelector tab="streams" ids={ids}>
      {(sel) => (
        <>
          {truncated && <p role="status" className="banner">Showing the first {ids.length} of {all.length} streams</p>}
          {sel.atCap && <p id={capReasonId('streams')}>{CAP_REASON}</p>}
          <div className={view.wide ? 'overview-grid wide' : 'overview-grid'}>
            <div>
              {visible && (ids.length === 0 ? (unheard ? null : <p>No streams with a PG ID are active. {PGID_HOW}</p>) : <Table ids={ids} ports={ports} labelOf={labelOf} sel={sel} />)}
              {visible && <FlowErrors ports={ports} labelOf={labelOf} />}
            </div>
            <div>
              <PgidChart title={(m) => `Streams: ${m.label}`} metrics={METRICS} metricLabel="Chart metric" intervalLabel="Streams interval" sel={sel} windowS={view.streamsWindowS} setWindowS={(n) => setView({ streamsWindowS: n })} metric={view.streamsMetric} setMetric={(m) => setView({ streamsMetric: m })} />
            </div>
          </div>
        </>
      )}
    </PgidSelector>
  )
}
