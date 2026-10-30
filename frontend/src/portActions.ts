import { acquire, pause, rate, reacquire, release, resetPorts, resume, setService, stop, type CaptureMsg, type PortView } from './api'

export type Selection =
  | { kind: 'root' }
  | { kind: 'port'; id: number }
  | { kind: 'profile'; port: number; profile: string }

// panelOnly: the result is shown in the Traffic tab, so the context menu leaves the action out.
// request: run() only asks the page to open the Traffic tab; it is not a command, so it never reports success.
export interface MenuAction { id: string; confirm?: string; label: string; enabled: boolean; danger?: boolean; request?: boolean; panelOnly?: boolean; run: () => Promise<unknown> }

// Start, Update and Assign need input (a multiplier, a profile), which lives in the Traffic tab. Their
// run() hands the request to the caller instead of calling the API.
export interface ActionHooks { capture?: CaptureMsg; request?: (id: 'start' | 'start_all' | 'update' | 'assign' | 'name', port: number | null) => void }

export const MULTI_PROFILE_HINT = 'read-only (multi-profile, P8)'
// Mirrors STATE_NAMES in backend/trex_web_gui/trexapi/ports.py (canon:CMT-8): TX and PCAP_TX transmit; PAUSE is paused TX.
export const TRANSMITTING = ['TX', 'PCAP_TX']
export const RUNNING = [...TRANSMITTING, 'PAUSE']

// State of the default profile `_`. The backend never publishes `_` while it is IDLE (the client skips IDLE,
// trex_stl_port.py:67-68, and ports.py _drop_idle_profiles removes it), so a missing `_` means IDLE, whatever the
// aggregate port state says: a foreign profile `bench` in TX leaves the port in TX with `_` absent.
// A port whose link is down reads state DOWN (ports.py) but keeps its per-profile states, so a transmitting port that
// loses link is still running: the state decides, and for DOWN any profile in a running state does (a foreign one too).
export const isRunning = (p: PortView): boolean =>
  RUNNING.includes(p.state) || (p.state === 'DOWN' && Object.values(p.profiles).some((s) => RUNNING.includes(s)))

export const defaultState = (p: PortView): string => p.profiles['_'] ?? 'IDLE'

// Reset removes every capture on the server (STLClient.reset), so every Reset confirmation names them; null when there are none.
// At most 10 captures exist, so every id is listed. "Unsaved" counts the packets still held on the server (each capture's `count`).
export function resetCaptureWarning(capture: CaptureMsg): string | null {
  const n = capture.captures.length
  if (n === 0) return null
  const packets = capture.captures.reduce((sum, c) => sum + c.count, 0)
  return `${n === 1 ? 'Capture' : 'Captures'} ${capture.captures.map((c) => c.id).join(', ')} will be removed from the server (${packets} unsaved ${packets === 1 ? 'packet' : 'packets'})`
}

const owned = (ports: PortView[]) => ports.filter((p) => p.owned)

function rootActions(ports: PortView[], readOnly: boolean, hooks?: ActionHooks): MenuAction[] {
  const all = ports.map((p) => p.id)
  const mine = owned(ports).map((p) => p.id)
  const running = owned(ports).some(isRunning)
  const mk = (id: string, label: string, enabled: boolean, run: () => Promise<unknown>, request?: boolean): MenuAction =>
    ({ id, label, enabled: enabled && !readOnly, run, ...(request ? { request } : {}) })
  const streams = owned(ports).some((p) => defaultState(p) === 'STREAMS')
  return [
    mk('acquire_all', 'Acquire all', all.length > 0, () => acquire({ ports: all })),
    mk('force_acquire_all', 'Force acquire all', all.length > 0, () => acquire({ ports: all, force: true })),
    mk('release_all', 'Release all', mine.length > 0, () => release({ ports: mine })),
    // enabled only for a port another session of this user holds (RC1-25): that is all the server action takes
    mk('reacquire', 'Re-acquire my ports', ports.some((p) => p.mine_elsewhere), () => reacquire()),
    mk('start_all', 'Start all', streams, () => { hooks?.request?.('start_all', null); return Promise.resolve() }, true),
    mk('stop_all', 'Stop all', running, () => stop({ ports: owned(ports).filter(isRunning).map((p) => p.id) })),
  ]
}

function portActions(p: PortView, readOnly: boolean, hooks?: ActionHooks): MenuAction[] {
  const ids = [p.id]
  const own = p.owned
  const st = p.state
  const dflt = defaultState(p)
  const captureWarning = hooks?.capture ? resetCaptureWarning(hooks.capture) : null
  const request = (id: 'start' | 'update' | 'assign') => () => { hooks?.request?.(id, p.id); return Promise.resolve() }
  const mk = (id: string, label: string, enabled: boolean, run: () => Promise<unknown>, danger?: boolean, req?: boolean): MenuAction =>
    ({ id, label, enabled: enabled && !readOnly, run, ...(danger ? { danger } : {}), ...(req ? { request: true } : {}) })
  return [
    mk('acquire', 'Acquire', !own, () => acquire({ ports: ids })),
    mk('force_acquire', 'Force acquire', true, () => acquire({ ports: ids, force: true })),
    mk('release', 'Release', own, () => release({ ports: ids })),
    // Filtered is set only from the Control tab; here a filtered port can be switched off, never to plain on.
    mk('service', p.service || p.service_filtered ? 'Disable service mode' : 'Enable service mode', own,
      () => setService({ ports: ids, enabled: !(p.service || p.service_filtered) })),
    mk('assign', 'Assign profile…', own, request('assign'), undefined, true),
    mk('start', dflt === 'IDLE' ? 'Start (assign a profile first)' : 'Start…', own && dflt === 'STREAMS', request('start'), undefined, true),
    mk('stop', 'Stop', own && isRunning(p), () => stop({ ports: ids })),
    mk('pause', 'Pause', own && dflt === 'TX', () => pause({ ports: ids })),
    mk('resume', 'Resume', own && dflt === 'PAUSE', () => resume({ ports: ids })),
    mk('update', 'Update…', own && dflt === 'TX', request('update'), undefined, true),
    // Reset force-acquires, so a foreign-owned port is fine.
    { ...mk('reset', 'Reset', true, () => resetPorts({ ports: ids }), true), ...(captureWarning ? { confirm: captureWarning } : {}) },
    // "Show rate at ×1" (TrafficTab): a validate-only RPC, meaningful once the port has streams, i.e. not IDLE/DOWN.
    // Built outside mk: naming a port edits the GUI's own file, so read-only mode, a foreign owner and a lost session do not gate it.
    { id: 'name', label: 'Name…', enabled: true, request: true, run: () => { hooks?.request?.('name', p.id); return Promise.resolve() } },
    { ...mk('rate', 'Show rate at ×1', own && st !== 'IDLE' && st !== 'DOWN', () => rate(ids[0])), panelOnly: true },
  ]
}

// connected: session.state === 'connected'. In any other state (LOST, DISCONNECTED) the port list is stale and every
// action is disabled; a click would only draw a 409.
export function actionsFor(sel: Selection, ports: PortView[], readOnly: boolean, connected: boolean, hooks?: ActionHooks): MenuAction[] {
  const acts = actionsForConnected(sel, ports, readOnly, hooks)
  return connected ? acts : acts.map((a) => (a.id === 'name' ? a : { ...a, enabled: false }))
}

function actionsForConnected(sel: Selection, ports: PortView[], readOnly: boolean, hooks?: ActionHooks): MenuAction[] {
  if (sel.kind === 'root') return rootActions(ports, readOnly, hooks)
  const id = sel.kind === 'port' ? sel.id : sel.port
  const p = ports.find((x) => x.id === id)
  if (!p) return []
  const acts = portActions(p, readOnly, hooks)
  if (sel.kind === 'profile' && sel.profile !== '_') {
    return acts.map((a) => (a.id === 'name' ? a : { ...a, enabled: false, label: `${a.label} (${MULTI_PROFILE_HINT})` }))
  }
  return acts
}
