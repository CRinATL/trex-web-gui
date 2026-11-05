import { useCallback, useEffect, useState } from 'react'
import { ApiError, getEmu, type EmuNsKey } from '../api'
import { EmuConnect } from '../components/emu/EmuConnect'
import { EmuClients } from '../components/emu/EmuClients'
import { EmuCounters } from '../components/emu/EmuCounters'
import { EmuProfile } from '../components/emu/EmuProfile'
import { EmuHelp } from '../components/emu/EmuHelp'
import { EmuNamespaces } from '../components/emu/EmuNamespaces'
import { EmuStatus } from '../components/emu/EmuStatus'
import { nsId } from '../emuFormat'
import { emuStore, useEmu } from '../emuStore'
import { useAppState } from '../state'

// The EMU page: the emulation server's connection, its namespaces, the clients of the selected one and the counters of the chosen scope. The WebSocket hello does not
// carry the EMU state, so the page asks for it as it mounts and again each time the socket comes back (a reload, or a reconnect that missed messages), and then follows the `emu` messages.
// A failed read is said on the page until the next successful read or hub message.
export function EmuPage() {
  const { backendOnline } = useAppState()
  const latest = useEmu((m) => m)
  const [failure, setFailure] = useState<{ text: string; at: unknown } | null>(null)
  const [selected, setSelected] = useState<EmuNsKey | null>(null)
  const [mac, setMac] = useState<number[] | null>(null)
  const clearSelection = useCallback(() => { setSelected(null); setMac(null) }, [])
  useEffect(() => {
    if (!backendOnline) return
    let live = true
    // A hub message that arrives while the GET is in flight is newer than its reply: the reply is then dropped.
    const before = emuStore.get()
    getEmu().then(
      (m) => { if (live && emuStore.get() === before) { setFailure(null); emuStore.push(m) } },
      (x: unknown) => { if (live) setFailure({ text: x instanceof ApiError ? x.message : String(x), at: before }) },
    )
    return () => { live = false }
  }, [backendOnline])
  return (
    <section className="emu-page">
      <div className="page-head">
        <h1>EMU</h1>
        <EmuHelp />
      </div>
      <EmuConnect />
      <EmuStatus />
      <EmuProfile />
      <EmuNamespaces selected={selected} onSelect={(n) => { if (!selected || nsId(selected) !== nsId(n)) setMac(null)
        setSelected({ vport: n.vport, tci: n.tci, tpid: n.tpid })
      }} />
      {selected && <EmuClients key={nsId(selected)} ns={selected} onGone={clearSelection} selectedMac={mac} onSelectClient={setMac} />}
      <EmuCounters ns={selected} mac={mac} />
      {failure && failure.at === latest && <p role="alert" className="error">Could not read the EMU state: {failure.text}</p>}
    </section>
  )
}
