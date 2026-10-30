import { useMemo } from 'react'
import { useMonitor } from '../../monitorStore'
import { templateRows } from '../../monitorLayouts'
import { cell } from '../../statsFormat'

// The Templates panel: each template's packets sent (from trex-global), and, when rx-check runs, the packets received, errors and jitter.
// A flagged row says `check` in text beside the red tint.
export function MonitorTemplates() {
  const names = useMonitor((m) => m?.templates ?? null)
  const g = useMonitor((m) => m?.global ?? null)
  const rx = useMonitor((m) => m?.rx_check ?? null)
  const rows = useMemo(() => templateRows(names ?? [], g, rx), [names, g, rx])
  return (
    <table className="streams" aria-label="Templates">
      <thead><tr><th>Name</th><th>Tx</th><th>Rx</th><th>Errors</th><th>Jitter</th></tr></thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} className={r.flagged ? 'tile-red' : undefined}>
            <th scope="row">{r.name}{r.flagged && <> <strong>check</strong></>}</th>
            <td>{cell(r.tx)}</td><td>{cell(r.rx)}</td><td>{cell(r.errors)}</td><td>{cell(r.jitter)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
