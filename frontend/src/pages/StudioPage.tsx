import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, listLibrary, type LibraryList } from '../api'
import { DiscardEditsDialog } from '../components/studio/DiscardEditsDialog'
import { ProfileEditor } from '../components/studio/ProfileEditor'
import { useOptionalLocation, useOptionalNavigate } from '../routerSafe'
import { ProfileLibrary } from '../components/studio/ProfileLibrary'

// `active`: the page is shown. App.tsx keeps it mounted while hidden, so the selection survives a page switch and the
// library is re-read each time the page is shown (and after every write): there is no push message for it.
export function StudioPage({ active = true }: { active?: boolean }) {
  const [list, setList] = useState<LibraryList | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const seq = useRef(0)
  // The open profile's embedded packet editor holds unsaved edits: selecting another profile asks first.
  const [unsaved, setUnsaved] = useState(false)
  const [switchTo, setSwitchTo] = useState<{ to: string | null } | null>(null)   // the profile (null: none) waiting for the user to agree to lose the edits
  const choose = (slug: string) => { if (unsaved && slug !== selected) setSwitchTo({ to: slug }); else setSelected(slug) }
  // `refresh` is a stable callback that finishes after a read, so it asks through refs.
  const unsavedRef = useRef(false), selectedRef = useRef<string | null>(null)
  useEffect(() => { unsavedRef.current = unsaved }, [unsaved])
  useEffect(() => { selectedRef.current = selected }, [selected])
  // `select`: undefined keeps the selection, a slug selects it, null clears it. A selection the list no longer holds is dropped.
  const refresh = useCallback(async (select?: string | null) => {
    const mine = ++seq.current
    try {
      const l = await listLibrary()
      if (mine !== seq.current) return
      setList(l); setError(null)
      const cur = selectedRef.current
      const want = select === undefined ? cur : select
      const next = want !== null && l.profiles.some((p) => p.slug === want) ? want : null
      // Another profile (New, Duplicate, Import, Delete, Control's studioSelect) with unsaved packet edits open: the open one
      // stays and the question is asked, as for a click on the list. If the open one is gone from the list, there is
      // nothing left to save to, and the switch is made.
      if (unsavedRef.current && next !== cur && cur !== null && l.profiles.some((p) => p.slug === cur)) setSwitchTo({ to: next })
      else setSelected(next)
    } catch (x) { if (mine === seq.current) setError(x instanceof ApiError || x instanceof Error ? x.message : String(x)) }
  }, [])
  // Control's New in Studio… and Copy to Studio navigate here with { studioSelect: slug }: taken once per navigation (its key).
  const location = useOptionalLocation()
  const navigate = useOptionalNavigate()
  const asked = (location?.state as { studioSelect?: unknown } | null)?.studioSelect
  const navKey = location?.key
  const taken = useRef<string | undefined>(undefined)
  useEffect(() => {
    if (!active) return
    const slug = typeof asked === 'string' && taken.current !== navKey ? asked : undefined
    if (slug !== undefined) taken.current = navKey
    const t = setTimeout(() => void refresh(slug), 0)    // off the effect body: the read's own setState happens when it lands
    return () => clearTimeout(t)
  }, [active, refresh, asked, navKey])
  const entries = list?.profiles ?? []
  const sel = entries.find((e) => e.slug === selected) ?? null
  return (
    <section className="studio-page">
      <h1>Studio</h1>
      <p><a href="/studio/packet" onClick={(e) => { if (navigate) { e.preventDefault(); navigate('/studio/packet') } }}>Packet editor</a></p>
      {error && <p role="alert" className="error">{error}</p>}
      <div className="studio-layout">
        <ProfileLibrary entries={entries} loadErrors={list?.load_errors ?? []} selected={selected} onSelect={choose} pinned={unsaved} onChanged={(s) => void refresh(s)} active={active} />
        <div className="studio-detail">
          {list && entries.length === 0 && list.load_errors.length === 0 && <p className="muted">No profiles yet. New… or Import… to add one.</p>}
          {sel && <ProfileEditor key={sel.slug} entry={sel} onChanged={() => void refresh()} onUnsaved={setUnsaved} active={active} />}
        </div>
      </div>
      {switchTo && <DiscardEditsDialog body="The open profile's packet editor has unsaved edits." onKeep={() => setSwitchTo(null)}
        onDiscard={() => { const { to } = switchTo; setSwitchTo(null); setSelected(to) }} />}
    </section>
  )
}
