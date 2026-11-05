import { useId, useState } from 'react'
import { ApiError, emuConnect, emuDisconnect } from '../../api'
import { EMU_DEFAULT_PORT, hostLabel, readEmuHosts, rememberEmuHost, useEmu } from '../../emuStore'
import { useAppState } from '../../state'

const failText = (x: unknown) => (x instanceof ApiError ? x.message : String(x))

// The EMU connection form: Host (with the hosts used before), Port, Connect and Disconnect. The host starts as the Control session's
// host, else the --server hint; the port as 4510. A refusal is shown as the backend worded it.
export function EmuConnect() {
  const { session, serverHint } = useAppState()
  const state = useEmu((m) => m?.state ?? 'disconnected')
  const [history, setHistory] = useState(readEmuHosts)
  const [host, setHost] = useState(() => session.params?.host ?? serverHint ?? '127.0.0.1')
  const [port, setPort] = useState(String(EMU_DEFAULT_PORT))
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const listId = useId()

  // Picking a datalist entry puts "host:port" into the Host field: split it back into the two fields.
  function onHost(v: string) {
    const hit = history.find((h) => hostLabel(h) === v.trim())
    if (hit) { setHost(hit.host); setPort(String(hit.port)) } else setHost(v)
  }
  const portNo = Number(port)
  const portOk = /^\d+$/.test(port) && portNo >= 1 && portNo <= 65535
  async function run(action: () => Promise<unknown>, onOk?: () => void) {
    setBusy(true); setFailure(null)
    try { await action(); onOk?.() } catch (x) { setFailure(failText(x)) } finally { setBusy(false) }
  }
  return (
    <div className="monitor-connect">
      <div className="form-row">
        <label>Host <input value={host} list={listId} onChange={(e) => onHost(e.target.value)} spellCheck={false} /></label>
        <datalist id={listId}>{history.map((h) => <option key={hostLabel(h)} value={hostLabel(h)} />)}</datalist>
        <label>Port <input className="narrow" inputMode="numeric" value={port} onChange={(e) => setPort(e.target.value)} /></label>
      </div>
      <div className="dialog-footer">
        <button type="button" disabled={busy || !host.trim() || !portOk || state === 'connecting'}
          onClick={() => void run(() => emuConnect(host.trim(), portNo), () => setHistory(rememberEmuHost({ host: host.trim(), port: portNo })))}>Connect</button>
        <button type="button" disabled={busy || state === 'disconnected'} onClick={() => void run(emuDisconnect)}>Disconnect</button>
      </div>
      {failure && <p role="alert" className="error">{failure}</p>}
    </div>
  )
}
