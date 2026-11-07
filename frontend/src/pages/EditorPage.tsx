import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Tabs, tabPanel } from '../ui/Tabs'
import { PacketPage } from './PacketPage'
import { StudioPage } from './StudioPage'

export type EditorTab = 'profiles' | 'packets'
const TABS: { id: EditorTab; label: string }[] = [{ id: 'profiles', label: 'Profiles' }, { id: 'packets', label: 'Packets' }]

// The Editor page: the profile library editor (Profiles) and the stand-alone packet editor (Packets) under one title.
// `tab` comes from the URL (App.tsx), `active`: the page is shown. App keeps this mounted while hidden.
export function EditorPage({ tab, active }: { tab: EditorTab; active: boolean }) {
  const navigate = useNavigate()
  // The packet editor asks the backend for its definitions as it mounts, so it is mounted at the first visit and kept (hidden) after.
  const [packetSeen, setPacketSeen] = useState(false)
  if (tab === 'packets' && !packetSeen) setPacketSeen(true)
  return (
    <section className="editor-page">
      <h1>Editor</h1>
      <Tabs label="Editor views" idPrefix="editor" tabs={TABS} selected={tab}
        onSelect={(t) => navigate(t === 'packets' ? '/editor/packet' : '/editor')} />
      <div {...tabPanel('editor', 'profiles', tab)}><StudioPage active={active && tab === 'profiles'} /></div>
      <div {...tabPanel('editor', 'packets', tab)}>{packetSeen && <PacketPage active={active && tab === 'packets'} />}</div>
    </section>
  )
}
