import { useMemo, useState } from 'react'
import { setConsole, type RpcMsg } from '../api'

// Each displayed body is cut at this many characters; Copy still copies everything that arrived.
const MAX_BODY = 8 * 1024
const errText = (x: unknown) => (x instanceof Error ? x.message : String(x))

// `on` and `includePolling` are the session's flags (state.session.rpc_console, .include_polling), not local
// state: the backend keeps capturing across a page reload, and the checkboxes must say so. Polling RPCs
// are dropped by the backend unless includePolling is set, so this component filters nothing by method.
export function RpcConsole({ entries, on, includePolling = false }: { entries: RpcMsg[]; on: boolean; includePolling?: boolean }) {
  const [filter, setFilter] = useState('')
  const [error, setError] = useState<string | null>(null)
  // stringify once per entries change, not on every render
  const rows = useMemo(() => entries.map((m) => ({ m, json: m.truncated !== undefined ? (m.body as string) : JSON.stringify(m.body) })), [entries])
  const shown = rows.filter(({ json }) => !filter || json.includes(filter))
  const line = (m: RpcMsg, body: string) => `${m.direction === 'request' ? '→' : '←'} ${body}`
  // the "64 KB" below is RPC_BODY_MAX in backend/trex_web_gui/sessions/trex_session.py: change them together
  // a body the backend cut (`truncated` is its full length) is already a string; its note goes last
  const note = (m: RpcMsg) => (m.truncated === undefined ? '' : `… (cut by the backend at 64 KB of ${m.truncated} characters)`)
  const text = shown.map(({ m, json }) => line(m, json.length > MAX_BODY
    ? `${json.slice(0, MAX_BODY)}${m.truncated === undefined ? '… (truncated; Copy copies the full body)' : '… (truncated)'}${note(m)}`
    : `${json}${note(m)}`)).join('\n')
  const full = shown.map(({ m, json }) => line(m, `${json}${note(m)}`)).join('\n')
  const send = (enabled: boolean, polling?: boolean) => { setError(null); (polling === undefined ? setConsole(enabled) : setConsole(enabled, polling)).catch((x: unknown) => setError(errText(x))) }
  const copy = () => { setError(null); navigator.clipboard.writeText(full).catch((x: unknown) => setError(`Copy failed: ${errText(x)}`)) }
  return (
    <section className="rpcconsole" aria-label="Raw JSON-RPC console">
      <div className="actions">
        <label><input type="checkbox" checked={on} onChange={(e) => send(e.target.checked)} /> Capture</label>
        <label title="Off by default: stats, port-status and event polls are not captured, so they cannot push your own commands out of the 500-entry buffer. That also hides the stats batches that Reset and Acquire send, and the PGID and utilization reads that Start, Reset and connect send.">
          <input type="checkbox" checked={includePolling} onChange={(e) => send(on, e.target.checked)} /> Include polling</label>
        <input placeholder="Filter text" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter" />
        <button onClick={copy} disabled={!shown.length}>Copy</button>
      </div>
      {error && <p role="alert" className="error">{error}</p>}
      <pre tabIndex={0}>{text || <span className="muted">{on ? 'Waiting for RPC traffic…' : 'Capture is off.'}</span>}</pre>
    </section>
  )
}
