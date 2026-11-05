import { emuNamespaces, type EmuNamespace, type EmuNsKey } from '../../api'
import { useEmu } from '../../emuStore'
import { nsId, nsLabel } from '../../emuFormat'
import { NS_POLL_MS, useEmuPoll } from '../../useEmuPoll'

// A tag list as text; all zeros means the namespace has no tags.
const tags = (v: readonly number[]) => (v.some((x) => x !== 0) ? v.join(', ') : 'none')

// The server's namespaces, re-read every 5 s and whenever the generation changes. A row's button selects the namespace whose clients
// the clients table lists; `ours` marks a namespace this GUI loaded.
export function EmuNamespaces({ selected, onSelect }: { selected: EmuNsKey | null; onSelect: (ns: EmuNamespace) => void }) {
  const generation = useEmu((m) => m?.generation ?? 0)
  const { data, error } = useEmuPoll(emuNamespaces, NS_POLL_MS, [generation])
  return (
    <section className="emu-namespaces">
      <h2>Namespaces</h2>
      {error && <p role="alert" className="error">Could not read the namespaces: {error.message}</p>}
      {data?.interrupted && <p className="muted" role="status">Another client was listing at the same time; showing what was read.</p>}
      {data && data.namespaces.length === 0 && <p className="muted">The server has no namespaces.</p>}
      {data && data.namespaces.length > 0 && (
        <table>
          <thead><tr><th>Port</th><th>VLAN tags (tci)</th><th>Tag types (tpid)</th><th>Clients</th><th>Plugins</th><th>Owner</th><th>Show</th></tr></thead>
          <tbody>
            {data.namespaces.map((n) => {
              const on = selected !== null && nsId(selected) === nsId(n)
              return (
                <tr key={nsId(n)}>
                  <td>{n.vport}</td><td>{tags(n.tci)}</td><td>{tags(n.tpid)}</td><td>{n.active_clients}</td>
                  <td>{n.plug_names.join(', ')}</td><td>{n.ours ? 'ours' : ''}</td>
                  <td><button type="button" aria-pressed={on} onClick={() => onSelect(n)}>Clients of {nsLabel(n)}</button></td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </section>
  )
}
