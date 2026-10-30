import { useMemo } from 'react'
import { useMonitor } from '../../monitorStore'
import { portRows } from '../../monitorLayouts'
import { cell, humanBps, humanPps } from '../../statsFormat'

// The Ports panel: the rates first, then the packet and error counts; the byte counts are folded away.
export function MonitorPorts() {
  const g = useMonitor((m) => m?.global ?? null)
  const rows = useMemo(() => portRows(g), [g])
  return (
    <div className="monitor-ports">
      <table className="ports-info" aria-label="Ports">
        <thead><tr><th>Port</th><th>Tx bps</th><th>Rx bps</th><th>Tx pps</th><th>Rx pps</th><th>Packets out</th><th>Packets in</th><th>Errors out</th><th>Errors in</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.port}>
              <th scope="row">{r.port}</th><td>{humanBps(r.tx_bps)}</td><td>{humanBps(r.rx_bps)}</td><td>{humanPps(r.tx_pps)}</td><td>{humanPps(r.rx_pps)}</td>
              <td>{cell(r.opackets)}</td><td>{cell(r.ipackets)}</td><td>{cell(r.oerrors)}</td><td>{cell(r.ierrors)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <details>
        <summary>Bytes</summary>
        <table className="ports-info" aria-label="Port bytes">
          <thead><tr><th>Port</th><th>Bytes out</th><th>Bytes in</th></tr></thead>
          <tbody>{rows.map((r) => <tr key={r.port}><th scope="row">{r.port}</th><td>{cell(r.obytes)}</td><td>{cell(r.ibytes)}</td></tr>)}</tbody>
        </table>
      </details>
    </div>
  )
}
