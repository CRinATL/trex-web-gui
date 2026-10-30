import { useRef, useState, useSyncExternalStore } from 'react'
import type { PortView } from '../api'
import { CLEAR_STATS_HINT, clearAll } from '../clearStats'
import { SetupsMenu } from './setups/SetupsMenu'
import { actionsFor, defaultState, TRANSMITTING, type MenuAction, type Selection } from '../portActions'
import { RunButton, type RunKind } from './RunButton'
import { dropPending, FormError, startFromForm, startSummary } from '../trafficStart'
import { trafficStore } from '../trafficStore'

const errText = (x: unknown) => (x instanceof Error ? x.message : String(x))

// Two labelled groups: Port N acts on the selected port (always shown, disabled at the root so the bar never jumps),
// All ports acts on every port. Start reads the port's Traffic form from trafficStore; a bad form goes to onFormError
// so the page can open the Traffic tab with the message. Start all asks the page for its confirmation dialog (onStartAll).
export function Toolbar({ ports, selection, readOnly, connected, statsConnected = false, onError, onFormError, onStartAll, onSaveSetup, onManageSetups, onDaemon }: {
  ports: PortView[]; selection: Selection; readOnly: boolean; connected: boolean; statsConnected?: boolean
  onError: (message: string | null) => void
  onFormError?: (port: number, message: string) => void
  onStartAll?: () => void
  onSaveSetup?: () => void; onManageSetups?: () => void   // the Setups menu is shown when the page owns the dialogs
  onDaemon?: () => void   // opens the Daemon dialog; enabled while disconnected too, because the daemon starts a TRex before anyone connects
}) {
  const onSelection = actionsFor(selection, ports, readOnly, connected)
  const root = actionsFor({ kind: 'root' }, ports, readOnly, connected)
  const find = (list: MenuAction[], ...ids: string[]) => list.find((a) => ids.includes(a.id))
  const portId = selection.kind === 'port' ? selection.id : selection.kind === 'profile' ? selection.port : null
  const sel = ports.find((p) => p.id === portId)
  const portState = sel ? defaultState(sel) : undefined
  const paused = portState === 'PAUSE'
  const sending = portState !== undefined && TRANSMITTING.includes(portState)
  const form = useSyncExternalStore(trafficStore.subscribe, () => trafficStore.get(portId ?? -1))
  const [busy, setBusy] = useState(false)
  const inFlight = useRef(false)

  // One request at a time; a failure goes to onFail (default onError).
  async function guarded(task: () => Promise<unknown>, onFail: (x: unknown) => void = (x) => onError(errText(x))) {
    if (inFlight.current) return
    inFlight.current = true; setBusy(true)
    try { await task(); onError(null) } catch (x) { onFail(x) } finally { inFlight.current = false; setBusy(false) }
  }
  // `before` runs first (it drops pending updates), then the action.
  const run = (a: MenuAction | undefined, before?: () => void) => {
    if (!a?.enabled) return
    void guarded(() => { before?.(); return a.run() })
  }
  const act = (label: string, a: MenuAction | undefined, before?: () => void, title?: string, kind?: RunKind) => kind
    ? <RunButton kind={kind} disabled={!a?.enabled || busy} title={title} onClick={() => run(a, before)}>{label}</RunButton>
    : <button type="button" disabled={!a?.enabled || busy} title={title} onClick={() => run(a, before)}>{label}</button>
  const noPort = portId === null ? 'Select a port' : undefined
  const mine = portId === null ? [] : [portId]
  const start = find(onSelection, 'start')
  const startAll = find(root, 'start_all')
  const toggle = paused ? find(onSelection, 'resume') : find(onSelection, 'pause')
  const ownership = sel?.owned ? find(onSelection, 'release') : find(onSelection, 'acquire')
  const label = portId === null ? 'Port —' : `Port ${portId}`
  const elsewhere = ports.filter((p) => p.mine_elsewhere).map((p) => p.id)

  function doStart() {
    if (!start?.enabled || portId === null) return
    void guarded(() => startFromForm(portId), (x) => {
      if (x instanceof FormError) onFormError?.(portId, x.message); else onError(errText(x))
    })
  }

  return (
    <div role="toolbar" aria-label="Control" className="toolbar">
      <div role="group" aria-label={label} className="tb-group">
        <span className="tb-label">{label}</span>
        {/* lit while the port transmits or is paused: the state sits where Start was */}
        <RunButton kind={paused ? 'hold' : 'go'} lit={sending || paused} litLabel={paused ? 'Paused' : 'Transmitting'}
          fits={['Transmitting', 'Paused']} disabled={!start?.enabled || busy}
          title={noPort ?? (sending || paused ? `Port ${portId} is ${paused ? 'paused' : 'transmitting'}` : startSummary(form))} onClick={doStart}>Start</RunButton>
        {act(paused ? 'Resume' : 'Pause', toggle, paused ? undefined : () => dropPending(mine), noPort, paused ? 'go' : 'hold')}
        {act('Stop', find(onSelection, 'stop'), () => dropPending(mine), noPort, 'stop')}
        {act(sel?.owned ? 'Release' : 'Acquire', ownership, undefined, noPort)}
      </div>
      <div role="group" aria-label="All ports" className="tb-group">
        <span className="tb-label">All ports</span>
        <RunButton kind="go" disabled={!startAll?.enabled || busy} onClick={() => onStartAll?.()}>Start all</RunButton>
        {act('Stop all', find(root, 'stop_all'), () => dropPending(ports.map((p) => p.id)), undefined, 'stop')}
        {act('Acquire all', find(root, 'acquire_all'))}
        {act('Release all', find(root, 'release_all'))}
        {/* shown only while another session of this user holds a port, e.g. a trex-web-gui that exited without releasing (RC1-25) */}
        {elsewhere.length > 0 && act('Re-acquire my ports', find(root, 'reacquire'), undefined,
          `Port${elsewhere.length > 1 ? 's' : ''} ${elsewhere.join(', ')} ${elsewhere.length > 1 ? 'are' : 'is'} held under your user name by another session`, 'go')}
      </div>
      <button type="button" disabled={!statsConnected} title={CLEAR_STATS_HINT} onClick={clearAll}>Clear stats</button>
      {onSaveSetup && onManageSetups && <SetupsMenu connected={connected} onSave={onSaveSetup} onManage={onManageSetups} />}
      {onDaemon && <button type="button" onClick={onDaemon}>TRex daemon…</button>}
    </div>
  )
}
