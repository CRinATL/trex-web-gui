import { useEffect, useRef, useState } from 'react'
import { ApiError, listLibrary, type LibraryEntry } from '../../api'
import { useHotkey } from '../../keys'
import { useDialogFocus } from '../useDialogFocus'
import { PY_NOTE, pySourceOf } from './libraryPick'

const errText = (x: unknown) => (x instanceof ApiError || x instanceof Error ? x.message : String(x))

// Picks a profile from the Studio library for the Traffic tab. A .py entry shows its source path and what Assign will send.
// `active` false (the page is hidden): Escape does nothing.
export function LibraryPickDialog({ onPick, onClose, active = true }: { onPick: (e: LibraryEntry) => void; onClose: () => void; active?: boolean }) {
  const ref = useDialogFocus<HTMLDivElement>('[data-autofocus]')
  const [entries, setEntries] = useState<LibraryEntry[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  useHotkey('escape', () => { if (active) onClose() })
  useEffect(() => {
    let live = true
    listLibrary().then((l) => { if (live) setEntries(l.profiles) }).catch((x) => { if (live) setError(errText(x)) })
    return () => { live = false }
  }, [])
  const firstRef = useRef<HTMLButtonElement>(null)
  const first = (entries ?? []).findIndex((e) => !e.load_error && !(pySourceOf(e) !== null && e.source_missing))
  // The list arrives after the dialog opened (focus went to Cancel): move it to the first entry that can be picked.
  useEffect(() => { firstRef.current?.focus() }, [entries])
  return (
    <div role="dialog" aria-modal="true" aria-label="Pick from the library" className="modal" ref={ref}>
      <h2>Pick from the library</h2>
      {entries === null && !error && <p className="muted">Loading…</p>}
      {entries && entries.length === 0 && <p className="muted">The library is empty. Add profiles in Studio.</p>}
      {entries && entries.length > 0 && (
        <ul className="setups-list">
          {entries.map((e, i) => {
            const src = pySourceOf(e)
            const blocked = !!e.load_error || (src !== null && e.source_missing)
            return (
              <li key={e.slug}>
                <button type="button" {...(i === first ? { ref: firstRef } : {})} disabled={blocked} onClick={() => onPick(e)}>{e.name}</button>{' '}
                <span className="muted">{e.stream_count} stream{e.stream_count === 1 ? '' : 's'}{e.load_error ? ` · ${e.load_error}` : src !== null && e.source_missing ? ' · source missing' : ''}</span>
                {src && <div className="muted">{src}: {PY_NOTE}</div>}
              </li>
            )
          })}
        </ul>
      )}
      {error && <p role="alert" className="error">{error}</p>}
      <div className="dialog-footer"><div className="end"><button type="button" data-autofocus onClick={onClose}>Cancel</button></div></div>
    </div>
  )
}
