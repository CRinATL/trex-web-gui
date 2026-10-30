import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { getXstats } from '../api'
import { xstatsStore } from '../xstatsStore'

const POLL_GAP_MS = 500

function usePageVisible(): boolean {
  const [v, setV] = useState(document.visibilityState === 'visible')
  useEffect(() => {
    const on = () => setV(document.visibilityState === 'visible')
    document.addEventListener('visibilitychange', on)
    on()
    return () => document.removeEventListener('visibilitychange', on)
  }, [])
  return v
}

// The port's xstats. Absolute server values are fetched by GET; the next request is scheduled 500 ms after the
// previous one settles, so none overlaps, and only while this tab is shown on a visible page and the session is connected.
export function XstatsTab({ port, visible, connected = true }: { port: number; visible: boolean; connected?: boolean }) {
  const pageVisible = usePageVisible()
  const on = visible && pageVisible && connected   // a lost session answers every GET with 409
  useSyncExternalStore(xstatsStore.subscribe, xstatsStore.version)
  const [failed, setFailed] = useState(false)
  const [notEmpty, setNotEmpty] = useState(true)
  const [filter, setFilter] = useState('')

  // The request in flight survives effect re-runs (tab or visibility toggled, StrictMode), so a new run waits for it.
  const inflight = useRef<Promise<void> | null>(null)
  useEffect(() => {
    if (!on) return
    let stop = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const once = async () => {
      const epoch = xstatsStore.epoch()   // a request sent before a connect_seq change must not refill the cleared store
      try {
        const v = await getXstats(port)
        if (stop || epoch !== xstatsStore.epoch()) return
        xstatsStore.push(v); setFailed(false)
      } catch { if (!stop) setFailed(true) }
    }
    const loop = () => {
      const p = once()
      inflight.current = p
      void p.then(() => {
        if (inflight.current === p) inflight.current = null
        if (!stop) timer = setTimeout(loop, POLL_GAP_MS)
      })
    }
    if (inflight.current) void inflight.current.then(() => { if (!stop) timer = setTimeout(loop, POLL_GAP_MS) })
    else loop()
    return () => { stop = true; clearTimeout(timer) }
  }, [on, port])

  const pins = xstatsStore.pins(port)
  const names = xstatsStore.names(port)
  const q = filter.toLowerCase()
  const shown = names.filter((n) => !pins.includes(n) && (!notEmpty || xstatsStore.relative(port, n) !== 0) && n.toLowerCase().includes(q))
  const list = [...pins.filter((n) => names.includes(n)), ...shown]

  return (
    <div>
      <div className="toolbar">
        <label><input type="checkbox" checked={notEmpty} onChange={(e) => setNotEmpty(e.target.checked)} /> Not empty</label>
        <label>Filter <input type="text" aria-label="Filter" value={filter} onChange={(e) => setFilter(e.target.value)} /></label>
        <button type="button" title="Browser-only: moves this browser's baseline; the server's counters are not touched" onClick={() => xstatsStore.reset(port)}>Reset counters</button>
        {failed && <span role="status">Counters are not updating</span>}
      </div>
      <p className="help">Values are the server's absolute counters minus the baseline set by Reset counters in this browser. Counters above 2^53 lose precision in the browser. A counter that goes backwards without a reconnect shows a negative value until Reset counters.</p>
      <table>
        <thead><tr><th>Pin</th><th>Counter</th><th>Value (since Reset counters)</th></tr></thead>
        <tbody>
          {list.map((n) => {
            const pinned = pins.includes(n)
            return (
              <tr key={n}>
                <td><input type="checkbox" aria-label={`Pin ${n}`} checked={pinned} onChange={() => (pinned ? xstatsStore.unpin(port, n) : xstatsStore.pin(port, n))} /></td>
                <td>{n}</td>
                <td>{xstatsStore.relative(port, n)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
