import { useEffect, useRef, useState } from 'react'
import { getLldp, monitorStart, monitorStop, type LldpNeighbour, type LldpReply } from '../../api'
import { captureStore } from '../../captureStore'
import { captureBlock, serviceOffPorts, turnServiceOff, turnServiceOn } from '../../captureGate'
import { errText } from '../../portGate'
import { useAppState } from '../../state'
import { PortPicker } from './PortPicker'
import { RunButton } from '../RunButton'

// The LLDP view is the capture monitor with a preset filter (802.1Q-tagged LLDP too): its Start is the monitor's Start, so
// it is refused while any monitor runs, and meanwhile the monitor's packet table shows only LLDP frames.
export const LLDP_BPF = 'ether proto 0x88cc or (vlan and ether proto 0x88cc)'
export const STOP_FIRST = 'Stop the current monitor first'
export const LLDP_RUNNING = 'LLDP monitor running'
// Stop does not turn service mode off: the capture gate turned it on. Once stopped, this tab turns it off on the ports this
// session owns (RC1-28); while the monitor runs it would cut the capture, so the button waits for Stop.
export const SERVICE_STAYS = 'Stopping leaves service mode on; Turn service mode off appears here once stopped.'
export const START_HINT = 'Start the LLDP monitor to see neighbours.'
// The firmware caveat, shown from the first render (spec §1): the poll that carries the backend's copy (trexapi/lldp.py
// NOTE) runs only while a monitor does. The test pins the two equal through lldpNeighbours.json.
export const LLDP_NOTE = 'Some NICs consume LLDP in firmware (Intel X710/XL710 under i40e is the known case), so a neighbour '
  + 'that sends LLDP may never appear here. Unverified on hardware: the test guest has no LLDP speaker.'
// An ID's subtype is shown beside it only when the value does not already say it: a MAC or an IP address reads as one.
// Every subtype stays in the cell's tooltip.
const EVIDENT_SUBTYPES = new Set(['MAC address', 'network address'])
const LIMIT = 100

// Sortable columns (RC1-18). Every sort ends with the default order, so ties never reorder between polls and a new
// neighbour lands in a place the header explains: port, VLAN (untagged first), system name, port ID.
type SortKey = 'port' | 'vlan' | 'name' | 'port_id' | 'port_desc' | 'chassis' | 'caps' | 'mgmt' | 'ttl'
const text = (a: string, b: string) => a.localeCompare(b, undefined, { numeric: true })
const COMPARE: Record<SortKey, (a: LldpNeighbour, b: LldpNeighbour) => number> = {
  port: (a, b) => a.port - b.port,
  vlan: (a, b) => (a.vlan ?? -1) - (b.vlan ?? -1),
  name: (a, b) => text(a.system_name ?? '', b.system_name ?? ''),
  port_id: (a, b) => text(a.port_id, b.port_id),
  port_desc: (a, b) => text(a.port_description ?? '', b.port_description ?? ''),
  chassis: (a, b) => text(a.chassis_id, b.chassis_id),
  caps: (a, b) => text(a.capabilities.join(', '), b.capabilities.join(', ')),
  mgmt: (a, b) => text(a.management_addresses.join(' '), b.management_addresses.join(' ')),
  ttl: (a, b) => a.ttl - b.ttl,
}
const DEFAULT_ORDER: SortKey[] = ['port', 'vlan', 'name', 'port_id']
const COLUMNS: { key: SortKey, label: string, num?: boolean }[] = [
  { key: 'port', label: 'Port', num: true }, { key: 'vlan', label: 'VLAN', num: true }, { key: 'name', label: 'System name' },
  { key: 'port_id', label: 'Port ID' }, { key: 'port_desc', label: 'Port description' }, { key: 'chassis', label: 'Chassis ID' },
  { key: 'caps', label: 'Capabilities' }, { key: 'mgmt', label: 'Management' }, { key: 'ttl', label: 'TTL (s)', num: true },
]
interface Sort { key: SortKey, dir: 1 | -1 }

function sorted(rows: LldpNeighbour[], sort: Sort): LldpNeighbour[] {
  const then = DEFAULT_ORDER.filter((k) => k !== sort.key)
  return [...rows].sort((a, b) => {
    const first = sort.dir * COMPARE[sort.key](a, b)
    return first !== 0 ? first : then.reduce((r, k) => r || COMPARE[k](a, b), 0)
  })
}
const POLL_MS = 2000

function IdCell({ what, value, subtype }: { what: string, value: string, subtype: string }) {
  return (
    <td title={`${what} subtype: ${subtype}`}>
      <span className="mono">{value}</span>
      {!EVIDENT_SUBTYPES.has(subtype) && <span className="muted lldp-subtype"> ({subtype})</span>}
    </td>
  )
}

// The neighbours the current monitor has seen, polled from the backend; the backend drops an entry once its TTL runs out.
// The page stays mounted while hidden (App.tsx), so it polls only while it matters: connected, a monitor running (the table
// belongs to it) and the page shown (`active`).
export function LldpNeighbours({ active = true }: { active?: boolean }) {
  const { capture, ports, session } = useAppState()
  const connected = session.state === 'connected'
  const monitorActive = capture.monitor !== null && !capture.monitor.gone
  const polling = connected && monitorActive && active
  const [rx, setRx] = useState<number[]>([])
  const [reply, setReply] = useState<LldpReply | null>(null)
  const [pollError, setPollError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sort, setSort] = useState<Sort>({ key: 'port', dir: 1 })
  const inFlight = useRef(false)

  useEffect(() => {
    if (!polling) return
    let live = true
    const poll = () => getLldp()
      .then((r) => { if (live) { setReply(r); setPollError(null) } })
      .catch((x) => { if (live) setPollError(errText(x)) })
    void poll()
    const timer = setInterval(() => void poll(), POLL_MS)
    return () => { live = false; clearInterval(timer) }
  }, [polling])

  const readOnly = session.params?.read_only ?? false
  const lldpRunning = monitorActive && capture.monitor?.bpf === LLDP_BPF
  // Connection and read-only first (captureBlock's order), then the running monitor, then service mode and the port choice.
  const gate = captureBlock({ connected, readOnly, monitorActive: false, rxPorts: rx, ports })
  const block = !connected || readOnly ? gate
    : monitorActive ? (lldpRunning ? LLDP_RUNNING : STOP_FIRST)
    : gate ?? (rx.length === 0 ? 'choose at least one Rx port' : null)

  async function command(fn: () => Promise<unknown>) {
    if (inFlight.current) return
    inFlight.current = true; setBusy(true); setError(null)
    try { await fn() }
    catch (x) { setError(errText(x)) }
    finally { inFlight.current = false; setBusy(false) }
  }
  const start = () => command(async () => {
    await monitorStart({ tx: [], rx, bpf: LLDP_BPF, limit: LIMIT })
    captureStore.clear()      // as the monitor's own Start: only an accepted Start empties the rows
  })
  // The Rx ports the service-mode line is about: the running LLDP monitor's, else the ones chosen.
  const shownRx = lldpRunning ? capture.monitor?.rx ?? [] : rx
  const off = serviceOffPorts(shownRx, ports)
  const on = shownRx.filter((id) => !off.includes(id))
  const ctl = connected && !readOnly
  // only ports this session owns: turning it off needs ownership, and this tab never acquires one
  const offable = on.filter((id) => ports.find((p) => p.id === id)?.owned)

  // Disconnected or no monitor: the last reply's note stays, its table and any poll error belonged to that connection
  // and monitor.
  const neighbours = connected && monitorActive ? reply?.neighbours ?? [] : []
  const resort = (key: SortKey) => setSort((s) => ({ key, dir: s.key === key ? (-s.dir as 1 | -1) : 1 }))
  return (
    <section className="lldp-neighbours" aria-label="LLDP">
      <h2>LLDP neighbours</h2>
      <div className="toolbar">
        <div className="form-grid"><PortPicker label="LLDP Rx ports" ports={ports} value={rx} onChange={setRx} disabled={monitorActive} /></div>
        <RunButton kind="go" lit={lldpRunning} litLabel={LLDP_RUNNING} disabled={busy || block !== null} title={block ?? undefined}
          onClick={() => void start()}>Start LLDP monitor</RunButton>
        <RunButton kind="stop" disabled={busy || !ctl || !lldpRunning}
          title={!connected ? 'not connected' : readOnly ? 'read-only session' : !lldpRunning ? 'the LLDP monitor is not running' : undefined}
          onClick={() => void command(monitorStop)}>Stop LLDP monitor</RunButton>
        {!monitorActive && ctl && off.length > 0 && (
          <button type="button" data-run="go" disabled={busy} onClick={() => void command(() => turnServiceOn(off))}>
            Turn service mode on for ports {off.join(', ')}
          </button>
        )}
        {!monitorActive && ctl && offable.length > 0 && (
          <button type="button" data-run="go" disabled={busy} onClick={() => void command(() => turnServiceOff(offable))}>
            Turn service mode off for port{offable.length > 1 ? 's' : ''} {offable.join(', ')}
          </button>
        )}
      </div>
      {shownRx.length > 0 && (
        <p className="muted" aria-label="LLDP service mode">
          Service mode: {[on.length && `on for port${on.length > 1 ? 's' : ''} ${on.join(', ')}`,
            off.length && `off for port${off.length > 1 ? 's' : ''} ${off.join(', ')}`].filter(Boolean).join('; ')}.
          {lldpRunning && ` ${SERVICE_STAYS}`}
        </p>
      )}
      {block && !lldpRunning && <p className="muted">{block}</p>}
      <p className="muted">{LLDP_NOTE}</p>
      {error && <p role="alert" className="error">{error}</p>}
      {connected && monitorActive && pollError && <p role="status" className="error">LLDP neighbours: {pollError}</p>}
      {!monitorActive && neighbours.length === 0 && <p className="muted">{START_HINT}</p>}
      {monitorActive && reply && neighbours.length === 0 && <p className="muted">No LLDP neighbours seen by the current monitor.</p>}
      {neighbours.length > 0 && (
        <div className="data-table-scroll">
          <table aria-label="LLDP neighbours" className="data-table lldp-table">
            <thead>
              <tr>{COLUMNS.map((c) => {
                const on = sort.key === c.key
                return (
                  <th key={c.key} className={c.num ? 'num' : undefined} aria-sort={on ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none'}>
                    <button type="button" className="th-sort" onClick={() => resort(c.key)}>
                      {c.label}{on && <span aria-hidden="true"> {sort.dir === 1 ? '▲' : '▼'}</span>}
                    </button>
                  </th>
                )
              })}</tr>
            </thead>
            <tbody>
              {sorted(neighbours, sort).map((n) => (
                <tr key={`${n.port}/${n.vlan ?? ''}/${n.chassis_id}/${n.port_id}`}>
                  <td className="num">{n.port}</td>
                  <td className="num">{n.vlan ?? ''}</td>
                  <td className="lldp-name"><span title={n.system_description ?? undefined}>{n.system_name ?? ''}</span></td>
                  <IdCell what="Port ID" value={n.port_id} subtype={n.port_id_subtype} />
                  <td>{n.port_description ?? ''}</td>
                  <IdCell what="Chassis ID" value={n.chassis_id} subtype={n.chassis_id_subtype} />
                  <td>{n.capabilities.join(', ')}</td>
                  <td className="mono">{n.management_addresses.map((a) => <div key={a}>{a}</div>)}</td>
                  <td className="num">{n.ttl}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
