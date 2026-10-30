import { useState } from 'react'
import type { PortIface, PortView, ServerInfo } from '../api'
import { ifaceText } from './ifaceText'
import type { Selection } from '../portActions'
import { portAria, portTitle, type PortLabel } from '../portLabels'
import { PortConfigTab } from './PortConfigTab'
import { PortControlTab } from './PortControlTab'
import { PortToolsTab } from './PortToolsTab'
import { XstatsTab } from './XstatsTab'
import { TrafficTab, type FocusTarget } from './TrafficTab'
import { Tabs, tabPanel } from '../ui/Tabs'

type Tab = 'control' | 'config' | 'traffic' | 'counters' | 'tools'
const TABS: { id: Tab; label: string }[] = [{ id: 'control', label: 'Control' }, { id: 'config', label: 'Config' }, { id: 'traffic', label: 'Traffic' }, { id: 'counters', label: 'Counters' }, { id: 'tools', label: 'Tools' }]
// seq changes on every request, so asking for the same thing twice still moves focus again
// 'name' selects the Control tab and focuses its Name field; 'profile' and 'mult' go to the Traffic tab.
export type PanelTarget = FocusTarget | 'name'
export interface PanelRequest { port: number; target: PanelTarget; seq: number; error?: string }

const NO_LABEL = () => null
const NO_IFACES: PortIface[] = []
const cell = (v: unknown) => (v === undefined || v === null || v === '' ? '–' : String(v))

// The bare id for a port the server has not listed yet; otherwise the heading every other place prints.
const portHeading = (ports: PortView[], id: number, labelOf: (p: PortView) => PortLabel | null) => {
  const port = ports.find((x) => x.id === id)
  if (!port) return <td>{id}</td>
  const lab = labelOf(port)
  return <td title={lab?.description || undefined} aria-label={portAria(port, lab)}>{portTitle(port, lab)}</td>
}

function SystemInfo({ info, ports, labelOf, ifaces, ifacesNote }: { info: ServerInfo; ports: PortView[]; labelOf: (p: PortView) => PortLabel | null; ifaces: PortIface[]; ifacesNote: string | null }) {
  const rows = info.ports.map((p, i): Record<string, unknown> & { _i: number } => ({ ...p, _i: typeof p.index === 'number' ? p.index : i })).sort((a, b) => a._i - b._i)
  return (
    <>
      <table className="info"><tbody>
        <tr><th>Version</th><td>{info.version} ({info.mode}, built {info.build_date})</td></tr>
        <tr><th>Host</th><td>{info.hostname}</td></tr>
        <tr><th>CPU</th><td>{info.core_type}</td></tr>
        <tr><th>Ports</th><td>{info.port_count}</td></tr>
      </tbody></table>
      {ifacesNote && <p className="muted">{ifacesNote}</p>}
      <table className="ports-info">
        <thead><tr><th>Port</th><th>Driver</th><th>Description</th><th>MAC</th><th>NUMA</th><th>PCI</th><th>Interface</th><th>State</th></tr></thead>
        <tbody>
          {rows.map((p) => {
            const t = ifaceText(ifaces.find((x) => x.port === p._i))
            return (
            <tr key={p._i}>{portHeading(ports, p._i, labelOf)}<td>{cell(p.driver)}</td><td>{cell(p.description)}</td><td className="mono">{cell(p.hw_mac)}</td><td>{cell(p.numa)}</td>
              <td className="mono">{cell(p.pci_addr)}</td>
              <td className={t.muted ? 'muted mono' : 'mono'} title={t.title}>{t.text}</td><td>{ports.find((x) => x.id === p._i)?.state ?? '–'}</td></tr>
            )
          })}
        </tbody>
      </table>
    </>
  )
}

export function PortPanel({ selection, ports, info, readOnly, connected, request, onResolved, active = true, labelOf = NO_LABEL, ifaces = NO_IFACES, ifacesNote = null }: {
  selection: Selection; ports: PortView[]; info: ServerInfo | null; readOnly: boolean; connected: boolean; request?: PanelRequest | null; onResolved?: () => void
  labelOf?: (p: PortView) => PortLabel | null
  ifaces?: PortIface[]; ifacesNote?: string | null
  active?: boolean   // false while another page is shown: the Traffic tab counts as hidden, so a countdown is cancelled
}) {
  const [tab, setTab] = useState<Tab>('control')
  const [seenSeq, setSeenSeq] = useState(request?.seq ?? 0)   // a remount must not replay an old request
  const [focusTarget, setFocusTarget] = useState<FocusTarget | null>(null)
  const [focusName, setFocusName] = useState(false)
  // adjusting state during render, as ControlPage does for port errors
  if (request && request.seq !== seenSeq) {
    setSeenSeq(request.seq)
    if (request.target === 'name') { setTab('control'); setFocusName(true) }
    else { setTab('traffic'); setFocusTarget(request.target) }
  }

  if (selection.kind === 'root') return info ? <SystemInfo info={info} ports={ports} labelOf={labelOf} ifaces={ifaces} ifacesNote={ifacesNote} /> : null
  const portId = selection.kind === 'port' ? selection.id : selection.port
  const port = ports.find((p) => p.id === portId)
  if (!port) return <p className="muted">Port {portId} is not available.</p>

  return (
    <div className="port-panel">
      <Tabs label={portTitle(port, labelOf(port))} idPrefix="port" tabs={TABS} selected={tab} onSelect={setTab} />
      <div {...tabPanel('port', 'control', tab)}>
        <PortControlTab key={portId} port={port} ports={ports} iface={ifaces.find((x) => x.port === portId)} readOnly={readOnly} connected={connected} selection={selection} onResolved={onResolved} focusName={focusName} onFocused={() => setFocusName(false)} />
      </div>
      <div {...tabPanel('port', 'config', tab)}>
        <PortConfigTab key={portId} port={port} readOnly={readOnly} connected={connected} selection={selection} onResolved={onResolved} />
      </div>
      {/* both panels stay mounted so switching tabs keeps the inspected profile, the values and the multiplier; a pending update countdown is the exception: hiding the Traffic tab cancels it and reverts the multiplier */}
      <div {...tabPanel('port', 'traffic', tab)}>
        <TrafficTab key={portId} port={port} selection={selection} ports={ports} readOnly={readOnly} connected={connected} visible={active && tab === 'traffic'} focusTarget={focusTarget} requestError={request?.port === portId && request.error ? { text: request.error, seq: request.seq } : null} onFocused={() => setFocusTarget(null)} onResolved={onResolved} />
      </div>
      <div {...tabPanel('port', 'counters', tab)}>
        <XstatsTab key={portId} port={port.id} visible={active && tab === 'counters'} connected={connected} />
      </div>
      <div {...tabPanel('port', 'tools', tab)}>
        <PortToolsTab key={portId} port={port} selection={selection} readOnly={readOnly} connected={connected} onResolved={onResolved} />
      </div>
    </div>
  )
}
