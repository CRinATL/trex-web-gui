import { useRef, useState } from 'react'
import { applyLayer, scan6, type PortView } from '../api'
import { layerView } from '../layerView'
import { errText } from '../portGate'
import { useBusy, useNeighbourActions, useNeighbours } from '../state'

const LEGACY_NOTE = 'TRex ports on the legacy stack do not answer IPv6; only other devices are listed.'

// IPv6 neighbours of one port. `why` is the reason the port's P3b commands are blocked right now, or null.
export function NeighbourTable({ port, why, onResolved }: { port: PortView; why: string | null; onResolved?: () => void }) {
  const id = port.id
  const busyKind = useBusy()[id]
  const list = useNeighbours(id)
  const { set, clear } = useNeighbourActions()
  const [error, setError] = useState<string | null>(null)
  const [restoreError, setRestoreError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [copyError, setCopyError] = useState<string | null>(null)
  const inFlight = useRef(false)
  const [working, setWorking] = useState<'scan' | 'l2' | null>(null)
  const scanning = busyKind === 'scan' || working === 'scan'
  const blocked = why !== null || working !== null
  const title = why ?? undefined

  async function guarded(kind: 'scan' | 'l2', job: () => Promise<void>) {
    if (inFlight.current) return
    inFlight.current = true; setWorking(kind); setError(null); setRestoreError(null); setNote(null)
    try { await job() } catch (x) { setError(errText(x)) } finally { inFlight.current = false; setWorking(null) }
  }
  const scan = () => guarded('scan', async () => {
    const r = await scan6({ port: id })
    set(id, r.neighbours)
    if (r.service_restore_error) setRestoreError(r.service_restore_error)
  })
  const toL2 = (mac: string) => guarded('l2', async () => {
    const r = await applyLayer({ port: id, mode: 'L2', dst_mac: mac, vlan: layerView(port).vlan })
    setNote(`L2 destination set to ${mac}`)
    if (r.service_restore_error) setRestoreError(r.service_restore_error)
    onResolved?.()
  })
  const copy = (text: string) => {
    setCopyError(null)
    // navigator.clipboard is absent on an insecure origin, which throws rather than rejects
    Promise.resolve().then(() => navigator.clipboard.writeText(text)).catch((x: unknown) => setCopyError(`Copy failed: ${errText(x)}`))
  }

  return (
    <div className="neighbours">
      <div className="actions">
        <button type="button" disabled={blocked} title={title} onClick={() => void scan()}>{scanning ? 'Scanning…' : 'Scan'}</button>
        <button type="button" disabled={list === undefined} onClick={() => clear(id)}>Clear</button>
      </div>
      {port.info.stack === 'legacy' && <p className="muted">{LEGACY_NOTE}</p>}
      {error && <p role="alert" className="error">{error}</p>}
      {restoreError && <p role="alert" className="error">Service mode could not be restored: {restoreError}</p>}
      {copyError && <p role="alert" className="error">{copyError}</p>}
      {note && <p role="status">{note}</p>}
      {list !== undefined && (list.length === 0 ? <p className="muted">No IPv6 neighbours found.</p> : (
        <table>
          <thead><tr><th>Type</th><th>MAC</th><th>IPv6</th><th></th></tr></thead>
          <tbody>{list.map((n) => (
            <tr key={`${n.mac}/${n.ipv6}`}>
              <td>{n.type}</td><td>{n.mac}</td><td>{n.ipv6}</td>
              <td>
                <button type="button" disabled={blocked} title={title} onClick={() => void toL2(n.mac)}>Set as L2 destination</button>
                <button type="button" onClick={() => copy(n.mac)}>Copy MAC</button>
                <button type="button" onClick={() => copy(n.ipv6)}>Copy IPv6</button>
              </td>
            </tr>
          ))}</tbody>
        </table>
      ))}
    </div>
  )
}
