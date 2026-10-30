import { useState } from 'react'
import type { DecodedRow } from '../api'
import { CaptureMonitor } from '../components/capture/CaptureMonitor'
import { LldpNeighbours } from '../components/capture/LldpNeighbours'
import { Recorders } from '../components/capture/Recorders'
import { SpoolFiles } from '../components/capture/SpoolFiles'
import { useSpoolListing } from '../components/capture/useSpoolListing'
import { PacketViewer } from '../components/packet/PacketViewer'
import { useAppState } from '../state'
import { Info } from '../ui/Info'
import { Tabs, tabPanel } from '../ui/Tabs'

type TabId = 'live' | 'recorders' | 'files' | 'lldp'

// `active`: the page is shown. App.tsx keeps it mounted while hidden, so whatever polls or re-asks the backend keys on it.
// All four panels stay mounted, so the monitor's form and the recorders' state survive a tab switch; the monitor and LLDP get
// `active` only while their own tab is shown, so a hidden tab polls and asks nothing.
export function CapturePage({ active = true }: { active?: boolean }) {
  const { capture } = useAppState()
  const spool = useSpoolListing()
  const [opened, setOpened] = useState<DecodedRow | null>(null)
  const [tab, setTab] = useState<TabId>('live')
  // The recorders are every capture except the monitor's own.
  const monitorId = capture.monitor?.id ?? null
  const recorders = capture.captures.filter((r) => r.id !== monitorId).length
  const tabs: { id: TabId; label: string }[] = [
    { id: 'live', label: 'Live' },
    { id: 'recorders', label: `Recorders (${recorders})` },
    { id: 'files', label: `Saved files (${spool.listing?.total ?? 0})` },
    { id: 'lldp', label: 'LLDP' },
  ]
  return (
    <section className="capture-page">
      <div className="page-head">
        <h1>Capture</h1>
        <Info label="capture">The monitor records the packets of the ports you choose; a recorder keeps them on the server until saved. Other capture tools, such as tcpdump on the host, may not see a port TRex is driving.</Info>
      </div>
      <Tabs label="Capture" idPrefix="capture" tabs={tabs} selected={tab} onSelect={setTab} />
      <div {...tabPanel('capture', 'live', tab)}><CaptureMonitor onOpen={setOpened} active={active && tab === 'live'} /></div>
      <div {...tabPanel('capture', 'recorders', tab)}><Recorders spool={spool} /></div>
      <div {...tabPanel('capture', 'files', tab)}><SpoolFiles spool={spool} /></div>
      <div {...tabPanel('capture', 'lldp', tab)}><LldpNeighbours active={active && tab === 'lldp'} /></div>
      {opened && <PacketViewer row={opened} onClose={() => setOpened(null)} />}
    </section>
  )
}
