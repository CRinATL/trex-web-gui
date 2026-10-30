// state.tsx exports the reducer and hook beside the provider by design (brief); fast refresh falls back to a full reload here
import { createContext, useCallback, useContext, useEffect, useMemo, useReducer, type ReactNode } from 'react'
import * as api from './api'
import { type BusyKind, type CaptureMsg, type CaptureSaveMsg, type CapturePktsMsg, type Neighbour, type PingLine, type PingMsg, type PortLabelsMsg, type PortView, type RpcMsg, type SetupLoadError, type TestSetup, type StatsMsg, type MonitorMsg, type MonitorTarget, type DaemonMsg, type DaemonHint, type PgidMsg, type ServerMsg, type SettingsMsg, type SessionSnapshot, type UtilMsg } from './api'
import { captureStore } from './captureStore'
import { dashboardPrefs } from './dashboardPrefs'
import { publishLog } from './eventsChannel'
import { monitorStore } from './monitorStore'
import { pgidStore } from './pgidStats'
import { setupSession } from './setupSession'
import { statsStore } from './stats'
import { trafficStore } from './trafficStore'
import { utilStore } from './utilStats'
import { xstatsStore } from './xstatsStore'
import { connectSocket } from './ws'

// The A2.9 log types; PING is one ping line. MARK is a divider the reducer adds when the connection changes.
export interface LogEntry { kind: 'SERVER_EVENT' | 'PING' | 'INFO' | 'WARNING' | 'ERROR' | 'MARK'; ts: number; message: string }
export interface AppState { backendOnline: boolean
  // False once the backend has said this browser's cookie is not accepted (GET /api/auth/status). True until told: the page
  // renders the shell first, and a backend that is merely down must not look like a refusal.
  authenticated: boolean
  session: SessionSnapshot; ports: PortView[]; rpc: RpcMsg[]; serverHint: string | null; serverPorts: { sync_port: number; async_port: number } | null
  // The event log, kept across reconnects (capped at LOG_CAP), and how many ERROR and WARNING entries came in since the Events page last saw it.
  log: LogEntry[]
  logUnseen: number
  // the latest 'port error' event (A3.10); seq changes on every one, so the Control page can alert on repeats
  portError: { port: number; seq: number } | null
  // monotonic across disconnects, so the first error after a reconnect always has a new seq
  portErrorSeq: number
  // the GUI's port names (Record<server, Record<key, {name, description}>>) and why the file could not be read, from port_labels messages
  portLabels: PortLabelsMsg['labels']
  portLabelsError: string | null
  // Every `ping` line of a port, runs one after the other (a run starts again at seq 1). Kept across page changes; a new connect clears it.
  pings: Record<number, PingMsg[]>
  // The newest ping run of a port whose POST has returned; a streamed line of that run or an older one arriving later is dropped.
  pingsDone: Record<number, number>
  // The ports a ping, scan or Apply holds right now, from `busy` messages (the whole map each time). Empty when none.
  busy: Record<number, BusyKind>
  // The last IPv6 scan of a port (absent: never scanned, [] : found none). Kept across page changes; a new connect clears it.
  neighbours: Record<number, Neighbour[]>
  // The server's capture list, the live monitor and Wireshark, from `capture` messages (the packets go to captureStore, not here).
  capture: CaptureMsg
  // The newest `capture_save` message per recorder id: a Save's progress, its file or its error.
  captureSave: Record<number, CaptureSaveMsg>
  // The saved test setups and the files that failed to load, from test_setups messages (the GUI's own files, kept across connects).
  setups: TestSetup[]
  setupLoadErrors: SetupLoadError[]
  // The preferences and saved connections with the data directory, from settings messages (null until the first). Pages that edit them keep their own copy.
  settings: Omit<SettingsMsg, 'type'> | null
  // From hello: whether the backend runs --monitor-only, and the host:port it was started with (null when it has none). The Monitor's
  // data is in monitorStore, which a Control connect never resets.
  monitorOnly: boolean
  monitorTarget: MonitorTarget | null
  // True once the first hello has arrived: until then monitorOnly is not known, so the shell renders nothing that could ask the backend for anything.
  helloSeen: boolean
  // The Daemon dialog's data: the newest `daemon` message (null until the first), the hello's target hint, and the stateful run the
  // Daemon dialog started last ({host, endsAt in ms}). The last is in memory only: a reload forgets it. Control connects never touch them.
  daemon: DaemonMsg | null
  daemonHint: DaemonHint | null
  lastStfRun: { host: string; endsAt: number } | null }
const CAP = 500   // rpc entries kept
const LOG_CAP = 2000   // log entries kept
// pingDone: a ping's POST returned; `run` is the run id the backend put on its streamed lines and on the response.
export type Action = { kind: 'msg'; msg: ServerMsg } | { kind: 'socket'; open: boolean } | { kind: 'auth'; authenticated: boolean }
  | { kind: 'neighbours'; port: number; list: Neighbour[] | null }   // list null: forget the port's scan
  | { kind: 'logSeen' } | { kind: 'logClear' }   // the Events page has seen the log; empty the log
  | { kind: 'stfRun'; run: AppState['lastStfRun'] }
  | { kind: 'pingDone'; port: number; run: number; count: number; lines: PingLine[]; ts: number }
const PING_CAP = 200   // lines kept per port

// eslint-disable-next-line react-refresh/only-export-components
export const initialState: AppState = {
  backendOnline: false,
  authenticated: true,
  session: { type: 'session', state: 'disconnected', params: null, info: null, error: null, rpc_console: false, include_polling: false, connect_seq: '', ifaces: [], ifaces_note: null },
  ports: [],
  log: [],
  logUnseen: 0,
  rpc: [],
  serverHint: null,
  serverPorts: null,
  portError: null,
  portErrorSeq: 0,
  portLabels: {},
  portLabelsError: null,
  pings: {},
  pingsDone: {},
  busy: {},
  neighbours: {},
  capture: { type: 'capture', ts: 0, monitor: null, captures: [], wireshark: { running: false, error: null } },
  captureSave: {},
  setups: [],
  setupLoadErrors: [],
  settings: null,
  monitorOnly: false,
  monitorTarget: null,
  helloSeen: false,
  daemon: null,
  daemonHint: null,
  lastStfRun: null,
}

// What a connect_seq change keeps: the socket state, the server hint (sent once per socket accept), and the
// monotonic port-error counter, the event log with its unseen count, and the port labels, test setups and settings (the GUI's own files, not the server's). Everything else starts over.
const KEEP = ['backendOnline', 'authenticated', 'serverHint', 'serverPorts', 'log', 'logUnseen', 'portErrorSeq', 'portLabels', 'portLabelsError', 'setups', 'setupLoadErrors', 'settings', 'monitorOnly', 'monitorTarget', 'helloSeen', 'daemon', 'daemonHint', 'lastStfRun'] as const satisfies readonly (keyof AppState)[]

// Adds one entry to the log (capped), counting it as unseen when it is an error or a warning.
function appendLog(s: AppState, e: LogEntry): AppState {
  const loud = e.kind === 'ERROR' || e.kind === 'WARNING'   // a MARK is not counted
  return { ...s, log: [...s.log, e].slice(-LOG_CAP), logUnseen: s.logUnseen + (loud ? 1 : 0) }
}

// The divider text for a session transition, or null when the snapshot is not one the user needs to see.
function connectionMark(m: SessionSnapshot, prev: SessionSnapshot['state'], newConnect: boolean): string | null {
  if (m.state === 'connected' && (newConnect || prev !== 'connected')) return `Connected to ${m.params ? `${m.params.host}:${m.params.sync_port}` : 'TRex'}`
  if (m.state === 'lost' && prev !== 'lost') return `Connection lost: ${m.error ?? 'unknown cause'}`
  if (m.state === 'disconnected' && prev === 'connected') return 'Disconnected'
  return null
}

// eslint-disable-next-line react-refresh/only-export-components
export function reducer(s: AppState, a: Action): AppState {
  if (a.kind === 'socket') return { ...s, backendOnline: a.open }
  if (a.kind === 'auth') return { ...s, authenticated: a.authenticated }
  if (a.kind === 'stfRun') return { ...s, lastStfRun: a.run }
  if (a.kind === 'logSeen') return s.logUnseen === 0 ? s : { ...s, logUnseen: 0 }
  if (a.kind === 'logClear') return { ...s, log: [], logUnseen: 0 }
  if (a.kind === 'neighbours') {
    const { [a.port]: _gone, ...rest } = s.neighbours
    void _gone
    return { ...s, neighbours: a.list === null ? rest : { ...rest, [a.port]: a.list } }
  }
  if (a.kind === 'pingDone') {
    // The response is authoritative: its lines replace whatever of this run was streamed (a line can be missed), in place,
    // or go last if none was streamed.
    const log = s.pings[a.port] ?? []
    const streamed = log.filter((l) => l.run === a.run)
    const run = a.lines.map((l, i): PingMsg => ({ ...l, type: 'ping', port: a.port, run: a.run, seq: i + 1, count: a.count,
      ts: streamed.find((x) => x.seq === i + 1)?.ts ?? a.ts }))
    const at = log.findIndex((l) => l.run === a.run)
    const rest = log.filter((l) => l.run !== a.run)
    const next = at < 0 ? [...rest, ...run] : [...rest.slice(0, at), ...run, ...rest.slice(at)]
    return { ...s, pings: { ...s.pings, [a.port]: next.slice(-PING_CAP) }, pingsDone: { ...s.pingsDone, [a.port]: Math.max(a.run, s.pingsDone[a.port] ?? 0) } }
  }
  const m = a.msg
  switch (m.type) {
    case 'session': {
      const prev = s.session.state
      const newConnect = (m.connect_seq ?? '') !== (s.session.connect_seq ?? '')
      let next: AppState
      if (newConnect) {
        // A new connect (the backend bumps it before the CONNECTING snapshot): nothing of the old one carries over except KEEP.
        next = { ...initialState, session: m }
        for (const k of KEEP) (next as unknown as Record<string, unknown>)[k] = s[k]
      } else if (!((m.state === 'disconnected' || m.state === 'connecting') && prev !== m.state)) {
        next = { ...s, session: m }
      } else {
        // The log stays: the user reads it after a disconnect. rpc survives a failed connect (connecting -> disconnected): that is where the failure is looked up
        next = { ...s, session: m, ports: [], portError: null, busy: {}, capture: initialState.capture, captureSave: {}, rpc: m.state === 'connecting' ? [] : s.rpc }
      }
      const text = connectionMark(m, prev, newConnect)
      return text === null ? next : appendLog(next, { kind: 'MARK', ts: Date.now() / 1000, message: text })
    }
    case 'hello': return { ...s, serverHint: m.server_hint, serverPorts: m.server_ports, monitorOnly: m.monitor_only ?? false, monitorTarget: m.monitor_target ?? null, daemonHint: m.daemon_hint ?? null, helloSeen: true }
    case 'daemon': return { ...s, daemon: m }
    case 'port_labels': return { ...s, portLabels: m.labels, portLabelsError: m.load_error }
    case 'test_setups': return { ...s, setups: m.setups, setupLoadErrors: m.load_errors }
    case 'settings': return { ...s, settings: { settings: m.settings, load_error: m.load_error, data_dir: m.data_dir } }
    case 'ports': return { ...s, ports: m.ports }
    case 'ping': {
      if (m.run <= (s.pingsDone[m.port] ?? 0)) return s   // a late line of a run whose response already filled the log
      return { ...s, pings: { ...s.pings, [m.port]: [...(s.pings[m.port] ?? []), m].slice(-PING_CAP) } }
    }
    case 'busy': return { ...s, busy: m.ports }
    case 'capture': return { ...s, capture: m }
    case 'capture_save': return { ...s, captureSave: { ...s.captureSave, [m.id]: m } }
    case 'rpc': return { ...s, rpc: [...s.rpc, m].slice(-CAP) }
    case 'log': return appendLog(s, { kind: m.level.toUpperCase() as LogEntry['kind'], ts: m.ts, message: m.message })
    case 'event': {
      const isPortError = m.name === 'port error' && typeof m.args[0] === 'number'
      const seq = s.portErrorSeq + (isPortError ? 1 : 0)
      return { ...appendLog(s, { kind: m.level === 'warning' ? 'WARNING' as const : 'SERVER_EVENT' as const, ts: m.ts, message: m.message ?? m.name }), portErrorSeq: seq, portError: isPortError ? { port: m.args[0] as number, seq } : s.portError }
    }
  }
  return s
}

// Without a provider (component tests) there is no hello to wait for, so the default context counts as having had one.
const Ctx = createContext<AppState>({ ...initialState, helloSeen: true })
// eslint-disable-next-line react-refresh/only-export-components
export const useAppState = () => useContext(Ctx)
// eslint-disable-next-line react-refresh/only-export-components
export const useBusy = (): AppState['busy'] => useContext(Ctx).busy
// eslint-disable-next-line react-refresh/only-export-components
export const usePingLog = (port: number): PingMsg[] => useContext(Ctx).pings[port] ?? NO_PINGS
const NO_PINGS: PingMsg[] = []
// eslint-disable-next-line react-refresh/only-export-components
export const useNeighbours = (port: number): Neighbour[] | undefined => useContext(Ctx).neighbours[port]
const NeighbourCtx = createContext<{ set: (port: number, list: Neighbour[]) => void; clear: (port: number) => void }>({ set: () => undefined, clear: () => undefined })
// eslint-disable-next-line react-refresh/only-export-components
export const useNeighbourActions = () => useContext(NeighbourCtx)

// Remembers the stateful run the Daemon dialog just started, for the Monitor hints on the Connect dialog and the Control page (stfDetect.ts).
const DaemonCtx = createContext<{ setLastStfRun: (run: AppState['lastStfRun']) => void }>({ setLastStfRun: () => undefined })
// eslint-disable-next-line react-refresh/only-export-components
export const useDaemonActions = () => useContext(DaemonCtx)

const LogCtx = createContext<{ clear: () => void; seen: () => void }>({ clear: () => undefined, seen: () => undefined })
// Empties the event log, or marks its errors and warnings as seen (zeroes logUnseen).
// eslint-disable-next-line react-refresh/only-export-components
export const useLogActions = () => useContext(LogCtx)

type PingRunner = (port: number, dst: string, count: number, pktSize: number) => Promise<Awaited<ReturnType<typeof api.ping>>>
const RunnerCtx = createContext<PingRunner>(() => Promise.reject(new Error('no AppStateProvider')))
// Sends a ping and, when the POST returns, replaces the run's streamed lines with the response's (a streamed line can be missed).
// Rejects as api.ping does, leaving whatever was streamed.
// eslint-disable-next-line react-refresh/only-export-components
export const usePingRunner = (): PingRunner => useContext(RunnerCtx)

// Module-level, like the stores it guards, so a remounted provider does not clear them.
let lastConnectSeq: string | null = null

export function AppStateProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState)
  const run = useCallback<PingRunner>(async (port, dst, count, pktSize) => {
    const r = await api.ping({ port, dst, count, pkt_size: pktSize })
    dispatch({ kind: 'pingDone', port, run: r.run, count, lines: r.lines, ts: Date.now() / 1000 })
    return r
  }, [])
  const neighbourActions = useMemo(() => ({
    set: (port: number, list: Neighbour[]) => dispatch({ kind: 'neighbours', port, list }),
    clear: (port: number) => dispatch({ kind: 'neighbours', port, list: null }),
  }), [])
  const daemonActions = useMemo(() => ({ setLastStfRun: (run: AppState['lastStfRun']) => dispatch({ kind: 'stfRun', run }) }), [])
  const logActions = useMemo(() => ({ clear: () => dispatch({ kind: 'logClear' }), seen: () => dispatch({ kind: 'logSeen' }) }), [])
  // Only this window publishes: a pop-out Events window renders no provider and shows what arrives here.
  useEffect(() => { publishLog(state.log) }, [state.log])
  useEffect(() => {
    const url = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/ws`
    // No GET /api/session on open: the backend pushes hello then the session snapshot on every accept
    // (api/app.py ws handler), and a GET reply could overwrite a newer pushed snapshot.
    const onMessage = (m: unknown) => {
      const t = (m as { type?: string }).type
      // stats, pgid and util arrive every second and live outside the reducer, so they never re-render the whole tree
      if (t === 'stats') { statsStore.push(m as StatsMsg); return }
      if (t === 'pgid') { pgidStore.push(m as PgidMsg); return }
      if (t === 'util') { utilStore.push(m as UtilMsg); return }
      // The Monitor is its own connection: no connect_seq below touches this store; only a message naming another target resets it.
      if (t === 'monitor') { monitorStore.push(m as MonitorMsg); return }
      // up to 50 rows every 0.5 s: outside the reducer, like stats
      if (t === 'capture_pkts') { captureStore.push(m as CapturePktsMsg); return }
      if (t === 'session') {
        const snap = m as SessionSnapshot
        // Only a changed connect_seq resets: the same snapshot repeated after a WebSocket reconnect keeps a Clear baseline.
        const seq = snap.connect_seq ?? ''
        if (seq !== lastConnectSeq) { lastConnectSeq = seq; statsStore.reset(); pgidStore.reset(); utilStore.reset(); xstatsStore.clear(); captureStore.reset(); trafficStore.reset(); dashboardPrefs.reset(); setupSession.reset() }
        statsStore.onSession(snap.state)
        pgidStore.onSession(snap.state)
        utilStore.onSession(snap.state)
      }
      dispatch({ kind: 'msg', msg: m as ServerMsg })
    }
    const onUnauthenticated = () => dispatch({ kind: 'auth', authenticated: false })
    // At boot and after any 401 (api.ts dispatches the event): ask the status route, which no token gate covers. A failed
    // call says nothing about the cookie, so it changes nothing.
    const checkAuth = () => { api.authStatus().then((ok) => dispatch({ kind: 'auth', authenticated: ok }), () => undefined) }
    checkAuth()
    window.addEventListener(api.UNAUTHENTICATED_EVENT, checkAuth)
    const stop = connectSocket(url, onMessage, (open) => dispatch({ kind: 'socket', open }), { authStatus: api.authStatus, onUnauthenticated })
    return () => { window.removeEventListener(api.UNAUTHENTICATED_EVENT, checkAuth); stop() }
  }, [])
  return <Ctx.Provider value={state}><RunnerCtx.Provider value={run}><NeighbourCtx.Provider value={neighbourActions}><LogCtx.Provider value={logActions}><DaemonCtx.Provider value={daemonActions}>{children}</DaemonCtx.Provider></LogCtx.Provider></NeighbourCtx.Provider></RunnerCtx.Provider></Ctx.Provider>
}
