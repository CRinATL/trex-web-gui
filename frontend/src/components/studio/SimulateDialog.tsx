import type { SimChain, SimReport, SimStream } from '../../api'
import { useHotkey } from '../../keys'
import { humanBps, humanPps } from '../../statsFormat'
import { useDialogFocus } from '../useDialogFocus'

const DASH = '–'
const SHOWN_ROWS = 100     // a 25 000-stream report lists its first rows; the totals and chains cover every stream
const MODES: Record<string, string> = { continuous: 'continuous', single_burst: 'single burst', multi_burst: 'multi burst' }

// The rate as the stream states it, before any conversion.
function rateGiven(s: SimStream): string {
  if (s.rate_type === 'pps') return humanPps(s.rate_value)
  if (s.rate_type === 'percentage') return `${s.rate_value} %`
  return `${humanBps(s.rate_value)} ${s.rate_type === 'bps_L1' ? 'L1' : 'L2'}`
}

// One chain as a line: its streams in order, then how it ends and, when known, how many packets it sends.
function chainText(c: SimChain): string {
  const path = c.steps.join(' → ')
  const packets = c.pkts === null ? '' : `: ${c.pkts} packets`
  if (c.end === 'stop') return c.pkts === null ? `${path} (runs until stopped)` : `${path}, then stop${packets}`
  const to = c.loop_to === null || c.loop_to === c.steps[0] ? '' : ` to ${c.loop_to}`
  return `${path}, loops${to} ${c.action_count === 0 ? 'forever' : `${c.action_count} time${c.action_count === 1 ? '' : 's'}`}${packets}`
}

// The Simulate report (backend studio/simulate.py report): the round trip, each stream's figures, the totals over the
// enabled streams, the chains, the streams nothing starts, and the notes on what the figures leave out.
// `active` false (the page is hidden): Escape does nothing.
export function SimulateDialog({ report, onClose, active = true }: { report: SimReport; onClose: () => void; active?: boolean }) {
  const ref = useDialogFocus<HTMLDivElement>('[data-autofocus]')
  useHotkey('escape', () => { if (active) onClose() })
  const { round_trip: rt, streams, totals, chains, unreachable, notes } = report
  // a total is unknown, not 0, when no enabled stream contributes a figure to it (a percentage stream gives none offline)
  const enabled = streams.filter((s) => s.enabled)
  const known = (pick: (s: SimStream) => number | null) => enabled.some((s) => pick(s) !== null)
  return (
    <div role="dialog" aria-modal="true" aria-label="Simulate" className="modal simulate-dialog" ref={ref}>
      <h2>Simulate</h2>
      <p>{rt.equal ? 'Round trip: identical' : 'Round trip: differs'}</p>
      {!rt.equal && <ul>{rt.differences.map((d, i) => <li key={i}>{d}</li>)}</ul>}
      <table className="streams">
        <thead><tr><th>#</th><th>Name</th><th>Length</th><th>Mode</th><th>Rate as given</th><th>pps</th><th>bps L1</th><th>bps L2</th><th>Packets</th></tr></thead>
        <tbody>
          {streams.slice(0, SHOWN_ROWS).map((s) => (
            <tr key={s.index}>
              <td>{s.index}</td><td>{s.name ?? ''}</td><td>{s.length}</td>
              <td>{MODES[s.mode] ?? s.mode}{s.enabled ? '' : ' (disabled)'}</td><td>{rateGiven(s)}</td>
              <td>{humanPps(s.pps)}</td><td>{humanBps(s.bps_l1)}</td><td>{humanBps(s.bps_l2)}</td><td>{s.pkts ?? DASH}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr><th scope="row" colSpan={5}>Total</th><td>{known((s) => s.pps) ? humanPps(totals.pps) : DASH}</td><td>{known((s) => s.bps_l1) ? humanBps(totals.bps_l1) : DASH}</td><td>{known((s) => s.bps_l2) ? humanBps(totals.bps_l2) : DASH}</td><td>{known((s) => s.pkts) ? totals.burst_pkts : DASH}</td></tr>
        </tfoot>
      </table>
      {streams.length > SHOWN_ROWS && <p className="muted">Showing the first {SHOWN_ROWS} of {streams.length} streams.</p>}
      {totals.percent > 0 && <p className="muted">Streams given as a percentage add {totals.percent} % of the line rate, which is not known offline, so the totals leave them out.</p>}
      {chains.length > 0 && (
        <>
          <h3>Chains</h3>
          <ul>{chains.map((c) => <li key={c.start}>{chainText(c)}</li>)}</ul>
        </>
      )}
      {unreachable.length > 0 && <p>Nothing starts {unreachable.join(', ')}.</p>}
      {notes.length > 0 && <ul className="muted">{notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
      <div className="dialog-footer"><div className="end"><button type="button" data-autofocus onClick={onClose}>Close</button></div></div>
    </div>
  )
}
