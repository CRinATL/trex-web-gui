import { useId, useState } from 'react'
import { ApiError, monitorConnect, monitorDisconnect } from '../../api'
import { useAppState } from '../../state'
import { useMonitor } from '../../monitorStore'

export const HOSTS_KEY = 'trex-web-gui.monitorHosts'
const HISTORY_MAX = 10
export const DEFAULT_PORT = 4500

interface HostEntry { host: string; port: number }
// The datalist text of an entry; an IPv6 host is bracketed so the port is readable.
const label = (e: HostEntry) => `${e.host.includes(':') ? `[${e.host}]` : e.host}:${e.port}`

// Entries are {host, port} pairs; anything else in storage (including the old "host:port" strings) is dropped.
function readHosts(): HostEntry[] {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(HOSTS_KEY) ?? '[]')
    if (!Array.isArray(v)) return []
    return v.filter((x): x is HostEntry => typeof x === 'object' && x !== null && typeof (x as HostEntry).host === 'string' && Number.isInteger((x as HostEntry).port))
      .map((x) => ({ host: x.host, port: x.port })).slice(0, HISTORY_MAX)
  } catch { return [] }
}
// Most recent first, once each.
function remember(entry: HostEntry): HostEntry[] {
  const next = [entry, ...readHosts().filter((h) => label(h) !== label(entry))].slice(0, HISTORY_MAX)
  try { localStorage.setItem(HOSTS_KEY, JSON.stringify(next)) } catch { /* storage unavailable: no history */ }
  return next
}

export const ALREADY = 'Already connected to this publisher; change Host or Port to switch'

const failText = (x: unknown) => (x instanceof ApiError ? x.message : String(x))

// The Monitor's connection form: Host (with the hosts used before), Port, Connect and Disconnect. This is the Monitor's own
// connection (a SUB socket on TRex's publisher port); it is independent of the Control session.
export function MonitorConnect() {
  const { monitorTarget, serverHint } = useAppState()
  const target = useMonitor((m) => m?.target ?? null)
  const [history, setHistory] = useState(readHosts)
  const [host, setHost] = useState(() => (target ?? monitorTarget)?.host ?? serverHint ?? '127.0.0.1')
  const [port, setPort] = useState(() => String((target ?? monitorTarget)?.port ?? DEFAULT_PORT))
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const listId = useId()

  // Picking a datalist entry puts "host:port" into the Host field: split it back into the two fields.
  function onHost(v: string) {
    const hit = history.find((h) => label(h) === v.trim())
    if (hit) { setHost(hit.host); setPort(String(hit.port)) } else setHost(v)
  }
  const portNo = Number(port)
  const portOk = /^\d+$/.test(port) && portNo >= 1 && portNo <= 65535
  // Connect to the publisher already subscribed to would only restart it and empty the panels; a different host or port
  // switches publisher, so that stays allowed. A stale subscription may be restarted.
  const state = useMonitor((m) => m?.state ?? 'disconnected')
  const same = target !== null && (state === 'connected' || state === 'connecting')
    && target.host === host.trim() && target.port === portNo
  async function run(action: () => Promise<unknown>, onOk?: () => void) {
    setBusy(true); setFailure(null)
    try { await action(); onOk?.() } catch (x) { setFailure(failText(x)) } finally { setBusy(false) }
  }
  return (
    <div className="monitor-connect">
      <div className="form-row">
        <label>Host <input value={host} list={listId} onChange={(e) => onHost(e.target.value)} spellCheck={false} /></label>
        <datalist id={listId}>{history.map((h) => <option key={label(h)} value={label(h)} />)}</datalist>
        <label>Port <input className="narrow" inputMode="numeric" value={port} onChange={(e) => setPort(e.target.value)} /></label>
      </div>
      <div className="dialog-footer">
        <button type="button" disabled={busy || !host.trim() || !portOk || same} title={same ? ALREADY : undefined}
          onClick={() => void run(() => monitorConnect(host.trim(), portNo), () => setHistory(remember({ host: host.trim(), port: portNo })))}>Connect</button>
        <button type="button" disabled={busy || target === null} onClick={() => void run(monitorDisconnect)}>Disconnect</button>
      </div>
      {failure && <p role="alert" className="error">{failure}</p>}
    </div>
  )
}
