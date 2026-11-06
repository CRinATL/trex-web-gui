import { useEffect, useState } from 'react'
import { ApiError, emuClients, macText, type EmuNsKey } from '../../api'
import { CLIENTS_PAGE, dgText, dotted, nsId, nsLabel, v6Lines } from '../../emuFormat'
import { useEmu } from '../../emuStore'
import { CLIENTS_POLL_MS, useEmuPoll } from '../../useEmuPoll'

// One page of the selected namespace's clients, 100 at a time, re-read every 5 s and whenever the generation changes (as the
// namespaces list is, so a removal does not leave a removed namespace's clients under "no namespaces"). A 409 no_namespace (the namespace is gone)
// calls onGone so the page clears the selection. With onSelectClient each row has a Select button (the client whose counters the counters panel can show). The page keys this component by namespace, so a new selection starts at offset 0.
export function EmuClients({ ns, onGone, selectedMac = null, onSelectClient }: {
  ns: EmuNsKey; onGone: () => void; selectedMac?: readonly number[] | null; onSelectClient?: (mac: number[]) => void
}) {
  const [offset, setOffset] = useState(0)
  const generation = useEmu((m) => m?.generation ?? 0)
  const { data, error } = useEmuPoll(() => emuClients(ns, offset, CLIENTS_PAGE), CLIENTS_POLL_MS, [nsId(ns), offset, generation])
  const gone = error instanceof ApiError && error.status === 409 && error.code === 'no_namespace'
  useEffect(() => { if (gone) onGone() }, [gone, onGone])
  const total = data?.total ?? 0
  return (
    <section className="emu-clients">
      <h2>Clients of {nsLabel(ns)}</h2>
      {error && !gone && <p role="alert" className="error">Could not read the clients: {error.message}</p>}
      {data?.interrupted && <p className="muted" role="status">Another client was listing at the same time; showing what was read.</p>}
      {data && (
        <>
          <p className="muted">{total === 0 ? 'No clients.' : data.clients.length === 0 ? 'No clients on this page.' : `Showing ${offset + 1}–${offset + data.clients.length} of ${total}`}</p>
          <div className="toolbar">
            <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - CLIENTS_PAGE))}>Previous page</button>
            <button type="button" disabled={offset + CLIENTS_PAGE >= total} onClick={() => setOffset(offset + CLIENTS_PAGE)}>Next page</button>
          </div>
          {data.clients.length > 0 && (
            <table>
              <thead><tr><th>MAC</th><th>IPv4</th><th>IPv4 gateway</th><th>Gateway MAC</th><th>MTU</th><th>IPv6</th><th>Plugins</th>{onSelectClient && <th>Counters</th>}</tr></thead>
              <tbody>
                {data.clients.map((c) => (
                  <tr key={macText(c.mac)}>
                    <td>{macText(c.mac)}</td><td>{dotted(c.ipv4)}</td><td>{dotted(c.ipv4_dg)}</td><td>{dgText(c)}</td><td>{c.ipv4_mtu}</td>
                    <td>{v6Lines(c).map((l) => <div key={l}>{l}</div>)}</td><td>{c.plug_names.join(', ')}</td>
                    {onSelectClient && (
                      <td>
                        <button type="button" aria-pressed={selectedMac !== null && macText(selectedMac) === macText(c.mac)} aria-label={`Select client ${macText(c.mac)}`} onClick={() => onSelectClient(c.mac)}>Select</button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </section>
  )
}
