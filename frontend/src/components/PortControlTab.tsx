import { useEffect, useRef, useState } from 'react'
import { resetCaptureWarning, RUNNING, type Selection } from '../portActions'
import { useAppState } from '../state'
import { resetPorts, setAttr, setService, SERVICE_MASK, type PortIface, type PortView } from '../api'
import { ifaceText } from './ifaceText'
import { PortNameField } from './PortNameField'

const errText = (x: unknown) => (x instanceof Error ? x.message : String(x))
const CONFIRM_MS = 5000
// Mirrors FLOW_CTRL_DICT (none 0, tx 1, rx 2, full 3) in backend/trex_web_gui/_vendor/trex/utils/constants.py:13-18 (canon:CMT-8).
const FC = ['NONE', 'TX', 'RX', 'FULL']
const MASK_BITS: [string, number][] = [['No TCP/UDP', SERVICE_MASK.NO_TCP_UDP], ['BGP', SERVICE_MASK.BGP], ['DHCP', SERVICE_MASK.DHCP],
  ['Transport', SERVICE_MASK.TRANSPORT], ['mDNS', SERVICE_MASK.MDNS]]
type ServiceMode = 'off' | 'on' | 'filtered'
const yn = (v: unknown) => (v === true ? 'yes' : v === false ? 'no' : '–')
const text = (v: unknown) => (v === undefined || v === null || v === '' ? '–' : String(v))
const active = (rx: Record<string, unknown>, key: string) => yn((rx[key] as { is_active?: boolean } | undefined)?.is_active)

export function PortControlTab({ port, ports, readOnly, connected = true, selection = { kind: 'port', id: port.id }, onResolved, iface, focusName, onFocused }: {
  port: PortView; ports?: PortView[]; iface?: PortIface; readOnly: boolean; connected?: boolean; selection?: Selection; onResolved?: () => void
  focusName?: boolean; onFocused?: () => void   // focus the Name field once (a Name… request), then say so
}) {
  const id = port.id
  const captureWarning = resetCaptureWarning(useAppState().capture)
  const info = port.info
  const driver = text(info.driver)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [led, setLed] = useState(false)   // the server reports no LED state, so this tracks what this session sent
  const serverMode: ServiceMode = port.service_filtered ? 'filtered' : port.service ? 'on' : 'off'
  const [mode, setMode] = useState<ServiceMode>(serverMode)
  const [seenMode, setSeenMode] = useState<ServiceMode>(serverMode)
  const [mask, setMask] = useState<number>(SERVICE_MASK.NO_TCP_UDP)
  // re-sync when the server reports a change (adjusting state during render)
  if (serverMode !== seenMode) { setSeenMode(serverMode); setMode(serverMode) }
  const [confirmReset, setConfirmReset] = useState(false)
  const [confirmLink, setConfirmLink] = useState(false)
  const inFlight = useRef(false)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const linkTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  useEffect(() => () => { clearTimeout(timer.current); clearTimeout(linkTimer.current) }, [])

  async function command(fn: () => Promise<unknown>): Promise<boolean> {
    if (inFlight.current) return false
    inFlight.current = true; setBusy(true); setError(null)
    try { await fn(); onResolved?.(); return true }
    catch (x) { setError(errText(x)); return false }
    finally { inFlight.current = false; setBusy(false) }
  }

  // Why a toggle is disabled, or null when it is usable. Connection and read-only come first: they override everything.
  const secondary = selection.kind === 'profile' && selection.profile !== '_'
  const why = (supported: boolean | null): string | null =>
    !connected ? 'not connected' : readOnly ? 'read-only session' : secondary ? 'select the default profile' : !port.owned ? 'acquire the port first'
      : supported === false ? `not supported by ${driver}` : null
  const gate = (supported: boolean | null) => { const w = why(supported); return { disabled: busy || w !== null, title: w ?? undefined } }
  // Reset takes the port from any owner, so ownership does not gate it
  const resetWhy = !connected ? 'not connected' : readOnly ? 'read-only session' : secondary ? 'select the default profile' : null
  const supported = (key: string) => info[key] === true

  function reset() {
    if (!confirmReset) {
      setConfirmReset(true)
      clearTimeout(timer.current)
      timer.current = setTimeout(() => setConfirmReset(false), CONFIRM_MS)
      return
    }
    clearTimeout(timer.current); setConfirmReset(false)
    void command(() => resetPorts({ ports: [id] }))
  }

  // Link down on a transmitting port can hang the v3.08 server (upstream issue #1207): the first click only warns.
  function linkChange(up: boolean) {
    if (!up && !confirmLink && RUNNING.includes(port.state)) {
      setConfirmLink(true)
      clearTimeout(linkTimer.current)
      linkTimer.current = setTimeout(() => setConfirmLink(false), CONFIRM_MS)
      return
    }
    clearTimeout(linkTimer.current); setConfirmLink(false)
    void command(() => setAttr({ ports: [id], link_up: up }))
  }

  function chooseMode(m: ServiceMode) {
    setMode(m)
    // Filtered stays selected until "Apply filter"; On/Off apply at once, so a failure puts the radio back.
    if (m !== 'filtered') void command(() => setService({ ports: [id], enabled: m === 'on' })).then((ok) => { if (!ok) setMode(serverMode) })
  }
  // Unticking a named bit while All (255) is on starts from the five named bits, so no unnamed bit is sent.
  const NAMED_BITS = MASK_BITS.reduce((m, [, b]) => m | b, 0)
  const toggleBit = (bit: number, on: boolean) => setMask(on ? mask | bit : (mask === SERVICE_MASK.ALL ? NAMED_BITS : mask) & ~bit)
  const applyFiltered = () => void command(() => setService({ ports: [id], enabled: false, filtered: true, mask }))

  const ifaceCell = ifaceText(iface)
  // Speed, Link, Owner and State are the chips above; Service, Promiscuous, Multicast and Flow control are controls below.
  const rows: [string, string][] = [
    ['Driver', driver], ['Description', text(info.description)], ['MAC', text(info.hw_mac)], ['PCI', text(info.pci_addr)], ['Interface', ifaceCell.text], ['Card', text(iface?.model ?? iface?.vendor_device)], ['Bound driver', text(iface?.driver)], ['NUMA', text(info.numa)],
    ['RX filter mode', text(port.rx_filter_mode)], ['VLAN tags', port.vlan.length ? port.vlan.join(', ') : 'none'],
    ['Capture port', active(port.rx_info, 'capture_port')], ['Grat ARP', active(port.rx_info, 'grat_arp')], ['RX queueing', active(port.rx_info, 'queue')],
  ]
  const link = port.link_up === null ? '–' : port.link_up ? 'up' : 'down'

  return (
    <div className="control-tab">
      <div className="chips" role="group" aria-label="Port status">
        <span className="chip">Speed {port.speed_gbps === null ? '–' : `${port.speed_gbps} Gbps`}</span>
        <span className={`chip${port.link_up === true ? ' ok' : port.link_up === false ? ' bad' : ''}`}>Link {link}</span>
        <span className="chip">Owner {port.owner || '(none)'}</span>
        <span className={`chip st-${port.state}`}>State {port.state}</span>
      </div>

      <details className="fold">
        <summary>Port details ({rows.length})</summary>
        <table className="info"><tbody>{rows.map(([k, v]) => <tr key={k}><th>{k}</th><td className={k === 'Interface' && ifaceCell.muted ? 'muted' : undefined}>{v}</td></tr>)}</tbody></table>
        {iface?.note && <p className="muted">{iface.note}</p>}
      </details>


      <div role="group" aria-label="Settings" className="settings-block">
      <PortNameField port={port} ports={ports ?? [port]} focus={focusName} onFocused={onFocused} />
      <hr />

      <h3>Attributes</h3>
      <div className="toggles">
        <label className="check"><input type="checkbox" checked={port.promiscuous === true} {...gate(supported('is_prom_supported'))}
          onChange={(e) => void command(() => setAttr({ ports: [id], promiscuous: e.target.checked }))} /> Promiscuous</label>
        <label className="check"><input type="checkbox" checked={port.multicast === true} {...gate(null)}
          onChange={(e) => void command(() => setAttr({ ports: [id], multicast: e.target.checked }))} /> Multicast</label>
        <label className="check"><input type="checkbox" checked={port.link_up === true} {...gate(supported('is_link_supported'))}
          onChange={(e) => linkChange(e.target.checked)} /> Link up</label>
        <label className="check"><input type="checkbox" checked={led} {...gate(supported('is_led_supported'))}
          onChange={(e) => { const on = e.target.checked; void command(async () => { await setAttr({ ports: [id], led_on: on }); setLed(on) }) }} /> LED</label>
        <label>Flow control <select value={port.fc_mode !== null && port.fc_mode >= 0 ? port.fc_mode : ''} {...gate(supported('is_fc_supported'))}
          onChange={(e) => void command(() => setAttr({ ports: [id], flow_ctrl: Number(e.target.value) }))}>
          {port.fc_mode === null || port.fc_mode < 0 ? <option value="">–</option> : null}
          {FC.map((n, i) => <option key={n} value={i}>{n}</option>)}</select></label>
      </div>

      {confirmLink && <p role="status" className="error">Link down while transmitting can hang the TRex v3.08 server (upstream issue #1207). Stop traffic first, or click again to proceed.</p>}

      <h3>Service mode</h3>
      <div role="radiogroup" aria-label="Service mode" className="toggles">
        {(['off', 'on', 'filtered'] as const).map((m) => (
          <label key={m} className="check"><input type="radio" name={`service-${id}`} checked={mode === m} {...gate(null)}
            onChange={() => chooseMode(m)} /> {m === 'off' ? 'Off' : m === 'on' ? 'On' : 'Filtered'}</label>
        ))}
      </div>
      {mode === 'filtered' && (
        <div className="toggles">
          {MASK_BITS.map(([name, bit]) => (
            <label key={name} className="check"><input type="checkbox" checked={(mask & bit) !== 0} {...gate(null)}
              onChange={(e) => toggleBit(bit, e.target.checked)} /> {name}</label>
          ))}
          <label className="check"><input type="checkbox" checked={mask === SERVICE_MASK.ALL} {...gate(null)}
            onChange={(e) => setMask(e.target.checked ? SERVICE_MASK.ALL : 0)} /> All</label>
          <button type="button" disabled={busy || why(null) !== null || mask === 0}
            title={why(null) ?? (mask === 0 ? 'choose at least one packet class' : undefined)} onClick={applyFiltered}>Apply filter</button>
        </div>
      )}
      <p className="muted">Filtered mode needs the TRex server started with --software.</p>
      </div>

      <h3>Reset</h3>
      <p className="muted">Reset takes the port from any owner, stops traffic and removes all streams.</p>
      <button type="button" className="danger" disabled={busy || resetWhy !== null} title={resetWhy ?? undefined} onClick={reset}>
        {confirmReset ? 'Click again to confirm reset' : 'Reset…'}
      </button>
      {confirmReset && captureWarning && <p role="status" className="error">{captureWarning}</p>}
      {error && <p role="alert" className="error">{error}</p>}
    </div>
  )
}
