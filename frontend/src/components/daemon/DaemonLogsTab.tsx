import { useEffect, useRef, useState } from 'react'
import { ApiError, daemonLog } from '../../api'

const REFRESH_MS = 2000

// The Logs tab: TRex's log or the daemon's own, in a <pre>, refreshed every 2 s while this tab is mounted (the dialog mounts it
// only while shown). A reply for the log that was selected before is dropped.
export function DaemonLogsTab({ connected }: { connected: boolean }) {
  const [which, setWhich] = useState<'trex' | 'daemon'>('trex')
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [tick, setTick] = useState(0)   // bumped by Refresh
  const inFlight = useRef(false)

  useEffect(() => {
    if (!connected) return
    let live = true
    const load = () => {
      if (inFlight.current) return
      inFlight.current = true
      daemonLog(which).then(
        (r) => { if (live) { setText(r.text); setError(null) } },
        (x) => { if (live) setError(x instanceof ApiError || x instanceof Error ? x.message : String(x)) },
      ).finally(() => { inFlight.current = false })
    }
    load()
    const t = setInterval(load, REFRESH_MS)
    return () => { live = false; clearInterval(t); inFlight.current = false }
  }, [which, connected, tick])

  return (
    <div className="daemon-logs">
      <div className="form-row">
        <label htmlFor="log-which">Log</label>
        <select id="log-which" value={which} onChange={(e) => { setText(''); setWhich(e.target.value as 'trex' | 'daemon') }}>
          <option value="trex">TRex log</option>
          <option value="daemon">Daemon log</option>
        </select>
        <button type="button" disabled={!connected} onClick={() => setTick((n) => n + 1)}>Refresh</button>
      </div>
      {!connected && <p className="muted">Not connected to a daemon.</p>}
      {error && <p role="alert" className="error">{error}</p>}
      <pre className="log-tail" aria-label="Log text" tabIndex={0}>{text}</pre>
    </div>
  )
}
