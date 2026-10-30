import { useCallback, useEffect, useState } from 'react'
import { ApiError, daemonDisconnect, start } from '../api'
import { DaemonDialog } from '../components/daemon/DaemonDialog'
import { ContextMenu } from '../components/ContextMenu'
import { OpenMonitorLink } from '../components/OpenMonitorLink'
import { DevicesTree } from '../components/DevicesTree'
import { PortPanel, type PanelRequest } from '../components/PortPanel'
import { SaveSetupDialog } from '../components/setups/SaveSetupDialog'
import { SetupDialog } from '../components/setups/SetupDialog'
import { Toolbar } from '../components/Toolbar'
import { actionsFor, defaultState, type Selection } from '../portActions'
import { portTitle, usePortDisplayLabelOf } from '../portLabels'
import { useOptionalLocation } from '../routerSafe'
import { useDialogFocus } from '../components/useDialogFocus'
import { useHotkey } from '../keys'
import { monitorStore, useMonitor } from '../monitorStore'
import { useAppState } from '../state'
import { stfRunOn, stfRunText } from '../stfDetect'
import { canUpdateRate, dropPending } from '../trafficStart'
import { trafficStore } from '../trafficStore'
import { RunButton } from '../components/RunButton'

interface Menu { sel: Selection; x: number; y: number }

const START_ALL_MULT = '1'   // the profile's own rate, ×1

// Start all has no per-port input to collect, so one confirmation stands in for the Traffic tab.
function StartAllDialog({ ports, busy, onConfirm, onCancel }: { ports: number[]; busy: boolean; onConfirm: () => void; onCancel: () => void }) {
  const ref = useDialogFocus<HTMLDivElement>('[data-autofocus]')
  useHotkey('escape', onCancel)
  return (
    <div role="dialog" aria-modal="true" aria-labelledby="start-all-title" className="modal" ref={ref}>
      <h2 id="start-all-title">Start all ports</h2>
      <p>Start {ports.length === 1 ? 'port' : 'ports'} {ports.join(', ')} at ×1 of each profile's rate?</p>
      <p className="muted">Each port's Traffic tab settings (rate, duration, Force) are not used.</p>
      <div className="dialog-footer">
        <div className="end">
          <button type="button" onClick={onCancel}>Cancel</button>
          <RunButton kind="go" data-autofocus disabled={busy || ports.length === 0} onClick={onConfirm}>Start all</RunButton>
        </div>
      </div>
    </div>
  )
}

// onConnect opens the Connect dialog, as the status bar's Connect… does.
export function ControlPage({ active = true, onConnect }: { active?: boolean; onConnect?: () => void }) {
  const { session, ports, portError, capture, setups, setupLoadErrors, daemon, lastStfRun, serverHint } = useAppState()
  const labelOf = usePortDisplayLabelOf(ports)
  const [error, setError] = useState<string | null>(null)
  const [selection, setSelection] = useState<Selection>({ kind: 'root' })
  const [menu, setMenu] = useState<Menu | null>(null)
  const [seenPortError, setSeenPortError] = useState(portError?.seq ?? 0)   // a remount must not replay an old error
  // A3.10: a 'port error' event also raises the alert line (adjusting state during render, not in an effect)
  if (portError && portError.seq !== seenPortError) {
    setSeenPortError(portError.seq)
    setError(`Port ${portError.port} reported an error; see Events`)
  }
  const location = useOptionalLocation()
  const [panelRequest, setPanelRequest] = useState<PanelRequest | null>(null)
  const [startAll, setStartAll] = useState(false)
  const [setupsDialog, setSetupsDialog] = useState<'save' | 'manage' | null>(null)
  const [startAllBusy, setStartAllBusy] = useState(false)
  const [daemonOpen, setDaemonOpen] = useState(false)
  const closeMenu = useCallback(() => setMenu(null), [])
  // A hidden page keeps its state but not its modal parts: no Escape hotkey and no focus trap behind another page.
  // Adjusting state during render, as for port errors above.
  // A Run goes on without its dialog; reopening shows the progress and result (setups.ts setupSession).
  if (!active && (menu || startAll || setupsDialog !== null)) { setMenu(null); setStartAll(false); setSetupsDialog(null) }
  // Save needs a session (it reads the Traffic forms of the connected ports); Manage works offline (Rename, Duplicate, Delete)
  if (setupsDialog === 'save' && !session.params) setSetupsDialog(null)
  // A hidden page closes the Daemon dialog too (adjusting state during render, as above); the effect below disconnects whenever it closes.
  if (!active && daemonOpen) setDaemonOpen(false)
  // The dialog's own Close disconnects too; a second disconnect is harmless, and this one covers a dialog closed by the page.
  useEffect(() => {
    if (!daemonOpen) return
    return () => { void daemonDisconnect().catch(() => undefined) }
  }, [daemonOpen])
  // Disconnected, and the hint host runs a stateful (-f) run: it has no command port, so say where to watch it (spec 13.5).
  const stfOnHint = useMonitor(() => session.state === 'disconnected' && !!serverHint && stfRunOn(serverHint, { daemon, monitor: monitorStore.get(), lastStfRun, now: Date.now() }))
  const info = session.info
  // This page is always mounted, so it drops a live update whose port can no longer take it even while another port (or
  // tab) is shown: otherwise a toolbar Pause then Resume would let the old countdown fire when its tab returns.
  const connected = session.state === 'connected'
  const live = connected || session.state === 'connecting'
  const readOnly = session.params?.read_only ?? false
  useEffect(() => {
    const stale = ports.filter((p) => trafficStore.get(p.id).pending && !canUpdateRate({ kind: 'port', id: p.id }, ports, readOnly, connected)).map((p) => p.id)
    if (stale.length) dropPending(stale)
  }, [ports, readOnly, connected])
  // A request only navigates (and never touches the alert line): the alert changes when a command resolves.
  const request = (id: 'start' | 'start_all' | 'update' | 'assign' | 'name', port: number | null) => {
    if (id === 'name' && port !== null) {
      setSelection({ kind: 'port', id: port })
      setPanelRequest((r) => ({ port, target: 'name', seq: (r?.seq ?? 0) + 1 }))
      return
    }
    if (port === null) { setSelection({ kind: 'root' }); setStartAll(true); return }
    setSelection({ kind: 'port', id: port })
    setPanelRequest((r) => ({ port, target: id === 'assign' ? 'profile' : 'mult', seq: (r?.seq ?? 0) + 1 }))
  }
  // Studio's Use in Control… navigates here with { studioAssign: { port } }: read once per navigation (its key), it selects the
  // port and opens its Traffic tab with the profile field focused. A remount does not replay the one it started under.
  const [seenNav, setSeenNav] = useState(location?.key)
  if (location && location.key !== seenNav) {
    setSeenNav(location.key)
    const want = (location.state as { studioAssign?: { port?: unknown } } | null)?.studioAssign?.port
    if (typeof want === 'number') request('assign', want)
  }
  // A bad toolbar Start: select the port first so its Traffic tab is mounted, then ask the panel to show the error there
  // (TrafficTab never shows an error carried by the request that first mounts it).
  const onFormError = (port: number, message: string) => {
    setSelection({ kind: 'port', id: port })
    setPanelRequest((r) => ({ port, target: 'mult', seq: (r?.seq ?? 0) + 1, error: message }))
  }
  const hooks = { request, capture }
  const startable = ports.filter((p) => p.owned && defaultState(p) === 'STREAMS').map((p) => p.id)
  async function doStartAll() {
    setStartAllBusy(true)
    try { await start({ ports: startable, mult: START_ALL_MULT }); setError(null); setStartAll(false) }
    catch (x) { setError(x instanceof ApiError ? x.message : String(x)); setStartAll(false) }
    finally { setStartAllBusy(false) }
  }
  const selectedPort = selection.kind === 'port' ? selection.id : selection.kind === 'profile' ? selection.port : null
  const selectedPortView = ports.find((p) => p.id === selectedPort)
  // The connection actions live in the status bar. The line replaces the root view and the view of a port that is gone;
  // a port the lost session still lists keeps its panel (I9).
  const reconnectOffered = session.state === 'disconnected' && !!session.error && !!session.params   // as StatusBar offers it
  // Disconnected, the line also carries its own Connect… (RC1-22), so the way in is next to where the user is looking.
  const connectHere = onConnect && <> or click <button type="button" data-run="go" onClick={onConnect}>Connect…</button></>
  const notConnected = session.state === 'disconnected'
    ? <>Not connected. Use {reconnectOffered ? 'Reconnect or Connect…' : 'Connect…'} in the status bar (Ctrl+Shift+C){connectHere}.</>
    : session.state === 'lost' ? 'Connection lost; Reconnect is in the status bar.' : null
  const showPanel = !(notConnected && !selectedPortView)
  return (
    <section>
      <h1>Control</h1>
      <Toolbar ports={ports} selection={selection} readOnly={readOnly} connected={live} statsConnected={connected}
        onError={setError} onFormError={onFormError} onStartAll={() => setStartAll(true)}
        onSaveSetup={() => setSetupsDialog('save')} onManageSetups={() => setSetupsDialog('manage')} onDaemon={() => setDaemonOpen(true)} />
      {stfOnHint && serverHint && <p role="status" className="banner">{stfRunText(serverHint)} <OpenMonitorLink /></p>}
      {error && <p role="alert" className="error">{error}</p>}
      <div className="control-grid">
        <div className="control-left">
          <DevicesTree host={info?.hostname ?? session.params?.host ?? ''} ports={ports} selection={selection} onSelect={setSelection} labelOf={labelOf}
            onContextMenu={(sel, x, y) => setMenu({ sel, x, y })} />
        </div>
        <div className="control-right">
          {notConnected && <p>{notConnected}</p>}
          {selectedPort !== null && selectedPortView && <h2>{portTitle(selectedPortView, labelOf(selectedPortView))}</h2>}
          {showPanel && selectedPort !== null && !selectedPortView && <h2>Port {selectedPort}</h2>}
          {showPanel && <PortPanel selection={selection} ports={ports} info={info} readOnly={readOnly} connected={connected} request={panelRequest} active={active} labelOf={labelOf} ifaces={session.ifaces} ifacesNote={session.ifaces_note} onResolved={() => setError(null)} />}
        </div>
      </div>
      {daemonOpen && <DaemonDialog onClose={() => setDaemonOpen(false)} />}
      {startAll && <StartAllDialog ports={startable} busy={startAllBusy} onConfirm={() => void doStartAll()} onCancel={() => setStartAll(false)} />}
      {setupsDialog === 'save' && session.params && <SaveSetupDialog params={session.params} ports={ports} setups={setups} onClose={() => setSetupsDialog(null)} />}
      {setupsDialog === 'manage' && <SetupDialog setups={setups} loadErrors={setupLoadErrors} params={session.params} ports={ports} readOnly={readOnly} onClose={() => setSetupsDialog(null)} />}
      {menu && <ContextMenu x={menu.x} y={menu.y} actions={actionsFor(menu.sel, ports, readOnly, connected, hooks).filter((a) => !a.panelOnly)} onClose={closeMenu} onError={setError} />}
    </section>
  )
}
