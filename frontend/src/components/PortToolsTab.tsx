import { useState } from 'react'
import type { PortView } from '../api'
import type { Selection } from '../portActions'
import { p3bBlock } from '../portGate'
import { useBusy } from '../state'
import { Tabs, tabPanel } from '../ui/Tabs'
import { NeighbourTable } from './NeighbourTable'
import { PingPanel } from './PingPanel'

type Tool = 'ping' | 'neighbours'
const TOOLS: { id: Tool; label: string }[] = [{ id: 'ping', label: 'Ping' }, { id: 'neighbours', label: 'IPv6 neighbours' }]

// The port's diagnostic tools, a vertical list beside the tool shown. Both panels stay mounted (hidden), so a ping in progress keeps its form.
// `why` is the port's P3b block, computed here as PortConfigTab does, and passed to both tools.
export function PortToolsTab({ port, selection, readOnly, connected, onResolved }: {
  port: PortView; selection: Selection; readOnly: boolean; connected: boolean; onResolved?: () => void
}) {
  const [tool, setTool] = useState<Tool>('ping')
  const busyKind = useBusy()[port.id]
  const why = p3bBlock({ port, connected, readOnly, selection, busyKind })
  return (
    <div className="side-layout tools-tab">
      <Tabs label="Tools" idPrefix="tools" tabs={TOOLS} selected={tool} onSelect={setTool} orientation="vertical" />
      <div>
        <div {...tabPanel('tools', 'ping', tool)}><PingPanel port={port} why={why} /></div>
        <div {...tabPanel('tools', 'neighbours', tool)}><NeighbourTable port={port} why={why} onResolved={onResolved} /></div>
      </div>
    </div>
  )
}
