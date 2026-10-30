import { useEffect, useRef, useState } from 'react'
import { ApiError, createProfile, getProfile, getTemplate, listLibrary, putStreams, SIDECAR_ENTRY_MAX, toStream, type DiffReply, type LibraryEntry, type PacketDoc, type SidecarEntry } from '../../api'
import { useHotkey } from '../../keys'
import { useOptionalNavigate } from '../../routerSafe'
import { PY_NOTE } from '../studio/libraryPick'
import { useDialogFocus } from '../useDialogFocus'

const entrySize = (e: SidecarEntry) => new TextEncoder().encode(JSON.stringify(e)).length
const errText = (x: unknown) => (x instanceof Error ? x.message : String(x))
const plural = (n: number, what: string) => `${n} ${what}${n === 1 ? '' : 's'}`
const NEW = ''   // the select's "New profile…" value; no slug is empty

// Adds the packet as a new last stream of a library profile: P4a's template stream (continuous, 1 pps, unnamed) with the
// packet and VM replaced, and the document in the stream's sidecar entry so that it reopens in the editor. `flags` are the
// stream's MAC-mode bits when the packet came from a preset. The success link opens Studio on the profile. A new profile
// can be named here (RC1-23), so an empty library never makes the user leave the packet to make one; once created it joins
// the list and stays chosen, so a failed add is retried into it rather than creating a second.
export function AddToProfileDialog({ doc, flags = null, onClose, active = true }: {
  doc: PacketDoc; flags?: number | null; onClose: () => void; active?: boolean
}) {
  const navigate = useOptionalNavigate()
  const ref = useDialogFocus<HTMLDivElement>('[data-autofocus]')
  const [entries, setEntries] = useState<LibraryEntry[] | null>(null)
  const [slug, setSlug] = useState(NEW)
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<{ slug: string; name: string; n: number; note: string | null } | null>(null)
  const live = useRef(true)
  useEffect(() => { live.current = true; return () => { live.current = false } }, [])
  useHotkey('escape', () => { if (active) onClose() })
  useEffect(() => {
    listLibrary().then((l) => {
      if (!live.current) return
      setEntries(l.profiles)
      setSlug((cur) => cur || (l.profiles.find((e) => !e.load_error)?.slug ?? ''))
    }, (x: unknown) => { if (live.current) setError(errText(x)) })
  }, [])

  const chosen = entries?.find((e) => e.slug === slug) ?? null
  const creating = entries !== null && slug === NEW
  const canAdd = creating ? name.trim() !== '' : !!chosen && !chosen.load_error
  // the name box appears after the list loads (an empty library) or when New profile… is chosen: it takes the focus then
  const nameRef = useRef<HTMLInputElement>(null)
  useEffect(() => { if (creating) nameRef.current?.focus() }, [creating])

  async function add() {
    if (!canAdd) return
    setBusy(true); setError(null)
    try {
      let target = chosen
      if (creating) {
        target = await createProfile(name.trim())
        const made = target
        if (live.current) { setEntries((l) => [...(l ?? []), made]); setSlug(made.slug) }
      }
      if (!target) return
      const sc = await toStream(doc)
      const base = await getTemplate()
      const stream = { ...base, packet: sc.packet, vm: sc.vm, ...(flags === null ? {} : { flags }) }
      const full: SidecarEntry = { editor: { doc, binary: sc.packet.binary, vm: sc.vm } }
      const tooBig = entrySize(full) > SIDECAR_ENTRY_MAX
      const entry: SidecarEntry = tooBig ? {} : full
      // a stale rev is re-read once and the write sent again; a second 409 is shown
      let reply: DiffReply
      for (let attempt = 0; ; attempt++) {
        const rev = (await getProfile(target.slug)).rev
        try { reply = await putStreams(target.slug, { rev, added: [stream], added_sidecar: [entry] }); break } catch (x) {
          if (!(x instanceof ApiError && x.status === 409 && x.code === 'stale') || attempt > 0) throw x
        }
      }
      if (live.current) setDone({
        slug: target.slug, name: target.name, n: reply.stream_count - 1,     // the table numbers from 0: the appended stream is the last index
        note: tooBig ? 'The packet document is too large to store with the stream: it will reopen from its bytes.' : null,
      })
    } catch (x) { if (live.current) setError(errText(x)) } finally { if (live.current) setBusy(false) }
  }

  return (
    <div role="dialog" aria-modal="true" aria-label="Add to profile" className="modal packet-dialog" ref={ref}>
      <h2>Add to profile</h2>
      {done === null && (
        <>
          {entries === null && !error && <p className="muted">Loading…</p>}
          {entries && entries.length === 0 && <p className="muted">The library has no profiles yet. Name one and the packet becomes its first stream.</p>}
          {entries && (
            <div className="form-grid">
              {entries.length > 0 && (
                <div className="form-row">
                  <label htmlFor="add-profile">Profile</label>
                  <select id="add-profile" value={slug} onChange={(e) => { setSlug(e.target.value); setError(null) }}>
                    {entries.map((e) => (
                      <option key={e.slug} value={e.slug} disabled={!!e.load_error}>
                        {e.name} ({plural(e.stream_count, 'stream')}){e.load_error ? ` — cannot be changed: ${e.load_error}` : ''}
                      </option>
                    ))}
                    <option value={NEW}>New profile…</option>
                  </select>
                </div>
              )}
              {creating && (
                <div className="form-row">
                  <label htmlFor="add-profile-name">New profile name</label>
                  <input id="add-profile-name" ref={nameRef} type="text" value={name}
                    onChange={(e) => { setName(e.target.value); setError(null) }}
                    onKeyDown={(e) => { if (e.key === 'Enter' && !busy) void add() }} />
                </div>
              )}
            </div>
          )}
          {chosen?.source?.kind === 'py' && <p className="muted">{PY_NOTE}</p>}
          {error && <p role="alert" className="error">{error}</p>}
          <div className="dialog-footer">
            <div className="end">
              <button type="button" data-run="go" disabled={busy || !canAdd} onClick={() => void add()}>{creating ? 'Create and add' : 'Add'}</button>
              <button type="button" data-autofocus onClick={onClose}>Cancel</button>
            </div>
          </div>
        </>
      )}
      {done && (
        <>
          <p role="status">Added as stream {done.n} of {done.name}.{' '}
            <a href="/studio" onClick={(e) => { e.preventDefault(); onClose(); navigate?.('/studio', { state: { studioSelect: done.slug } }) }}>Open {done.name} in Studio</a></p>
          {done.note && <p role="status" className="muted">{done.note}</p>}
          <div className="dialog-footer"><div className="end"><button type="button" data-autofocus onClick={onClose}>Close</button></div></div>
        </>
      )}
    </div>
  )
}
