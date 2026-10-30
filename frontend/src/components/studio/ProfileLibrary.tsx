import { useRef, useState, type KeyboardEvent } from 'react'
import { ApiError, createProfile, deleteProfile, duplicateProfile, getProfile, renameProfile, type LibraryEntry, type LibraryList } from '../../api'
import { useAppState } from '../../state'
import { AssignToPortDialog } from './AssignToPortDialog'
import { ImportDialog } from './ImportDialog'

const errText = (x: unknown) => (x instanceof ApiError || x instanceof Error ? x.message : String(x))
type Edit = { kind: 'new' | 'rename' | 'duplicate'; name: string }
const TITLE: Record<Edit['kind'], string> = { new: 'New profile', rename: 'Rename profile', duplicate: 'Duplicate profile' }
const VERB: Record<Edit['kind'], string> = { new: 'Create', rename: 'Rename', duplicate: 'Duplicate' }

// One option's muted line: '<n> streams · <source kind>', then the problem as text (a load error, or a missing source).
const meta = (e: LibraryEntry): string => {
  const parts = [`${e.stream_count} stream${e.stream_count === 1 ? '' : 's'}`, e.source?.kind ?? 'no source']
  if (e.load_error) parts.push(e.load_error)
  else if (e.source_missing) parts.push('source missing')
  return parts.join(' · ')
}

// The library pane: the profiles as a listbox, the New… bar, and the selected entry's Rename…, Duplicate… and Delete.
// `onChanged` is called after every write: no argument keeps the selection, a slug selects that entry, null clears it.
// `active` false: the Studio page is hidden, so the import dialog takes no keys.
// `pinned`: the open profile's packet editor has unsaved edits, so Rename… and Delete (which would drop them) are off.
const PINNED = 'The packet has unsaved edits: save or cancel them first.'
export function ProfileLibrary({ entries, loadErrors, selected, onSelect, onChanged, active = true, pinned = false }: {
  entries: LibraryEntry[]; loadErrors: LibraryList['load_errors']; selected: string | null
  onSelect: (slug: string) => void; onChanged: (select?: string | null) => void; active?: boolean; pinned?: boolean
}) {
  const { setups, ports } = useAppState()
  const [edit, setEdit] = useState<Edit | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [importing, setImporting] = useState(false)
  const [assigning, setAssigning] = useState(false)
  const listRef = useRef<HTMLUListElement>(null)
  const sel = entries.find((e) => e.slug === selected) ?? null

  function select(slug: string) {
    if (slug === selected) return
    setEdit(null); setConfirmDelete(false); setError(null)
    onSelect(slug)
  }
  function onListKey(e: KeyboardEvent<HTMLUListElement>) {
    if (entries.length === 0) return
    const at = sel ? entries.indexOf(sel) : -1
    const to = e.key === 'ArrowDown' ? Math.min(at + 1, entries.length - 1) : e.key === 'ArrowUp' ? Math.max(at - 1, 0)
      : e.key === 'Home' ? 0 : e.key === 'End' ? entries.length - 1 : -1
    if (to < 0) return
    e.preventDefault()
    select(entries[to].slug)
    listRef.current?.querySelector<HTMLElement>(`[data-slug="${entries[to].slug}"]`)?.focus()
  }
  async function run(work: () => Promise<string | null | undefined>) {
    setBusy(true); setError(null)
    try { const to = await work(); setEdit(null); setConfirmDelete(false); onChanged(to) }
    catch (x) { setError(errText(x)) }
    finally { setBusy(false) }
  }
  const doEdit = () => edit && run(async () => {
    const name = edit.name.trim()
    if (edit.kind === 'new') return (await createProfile(name)).slug
    if (!sel) return undefined
    if (edit.kind === 'duplicate') return (await duplicateProfile(sel.slug, name)).slug
    return (await renameProfile(sel.slug, name, (await getProfile(sel.slug)).rev)).slug
  })
  const doDelete = () => sel && run(async () => { await deleteProfile(sel.slug); return null })
  const renamePinned = pinned && edit?.kind === 'rename'      // a Rename form opened while clean must not submit once the edits are unsaved
  // Saved test setups name a profile by its path; a rename moves the file, so they would no longer find it.
  const pointing = sel ? setups.filter((s) => s.ports.some((p) => p.profile.path === sel.path)).length : 0

  return (
    <div className="studio-library">
      <ul role="listbox" aria-label="Profiles" className="setups-list" ref={listRef} onKeyDown={onListKey}>
        {entries.map((e) => (
          <li key={e.slug} role="option" aria-selected={e.slug === selected} data-slug={e.slug} tabIndex={e.slug === selected || (!sel && e === entries[0]) ? 0 : -1}
            onClick={() => select(e.slug)}>
            <span className="setup-name">{e.name}</span>{' '}
            <span className="muted setup-meta">{meta(e)}</span>
          </li>
        ))}
        {loadErrors.map((e) => (
          <li key={e.file} role="option" aria-selected="false" aria-disabled="true"><span className="error">{e.file}: {e.error}</span></li>
        ))}
      </ul>
      <div className="setups-buttons" role="toolbar" aria-label="Library actions">
        <button type="button" disabled={busy || !!edit} onClick={() => { setConfirmDelete(false); setError(null); setEdit({ kind: 'new', name: '' }) }}>New…</button>
        <button type="button" disabled={busy || !!edit} onClick={() => { setConfirmDelete(false); setError(null); setImporting(true) }}>Import…</button>
      </div>
      {sel && (
        <div className="setups-buttons" role="toolbar" aria-label={`${sel.name} actions`}>
          <button type="button" disabled={busy || !!edit || pinned} title={pinned ? PINNED : undefined} onClick={() => { setConfirmDelete(false); setError(null); setEdit({ kind: 'rename', name: sel.name }) }}>Rename…</button>
          <button type="button" disabled={busy || !!edit} onClick={() => { setConfirmDelete(false); setError(null); setEdit({ kind: 'duplicate', name: `${sel.name} copy` }) }}>Duplicate…</button>
          <button type="button" disabled={busy || !!edit || pinned} title={pinned ? PINNED : undefined} onClick={() => { setError(null); setConfirmDelete(true) }}>Delete</button>
          <button type="button" disabled={busy || !!edit || !!sel.load_error} onClick={() => { setConfirmDelete(false); setError(null); setAssigning(true) }}>Use in Control…</button>
        </div>
      )}
      {confirmDelete && sel && (
        <p role="alert">Delete profile {sel.name}? <button type="button" className="danger" disabled={busy || pinned} title={pinned ? PINNED : undefined} onClick={() => void doDelete()}>Confirm delete</button>{' '}
          <button type="button" onClick={() => setConfirmDelete(false)}>Cancel</button></p>
      )}
      {edit && (
        <div role="group" aria-label={TITLE[edit.kind]}>
          <label>Name <input autoFocus value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })}
            onKeyDown={(e) => { if (e.key === 'Enter' && edit.name.trim() && !busy && !renamePinned) void doEdit(); if (e.key === 'Escape') { setEdit(null); setError(null) } }} /></label>{' '}
          <button type="button" onClick={() => { setEdit(null); setError(null) }}>Cancel</button>{' '}
          <button type="button" disabled={busy || !edit.name.trim() || renamePinned} title={renamePinned ? PINNED : undefined} onClick={() => void doEdit()}>{VERB[edit.kind]}</button>
          {edit.kind === 'rename' && pointing > 0 && (
            <p className="muted">{pointing} test setup{pointing === 1 ? '' : 's'} point{pointing === 1 ? 's' : ''} at this profile; they will need the new path.</p>
          )}
        </div>
      )}
      {error && <p role="alert" className="error">{error}</p>}
      {assigning && sel && <AssignToPortDialog entry={sel} ports={ports} active={active} onClose={() => setAssigning(false)} />}
      {importing && <ImportDialog active={active} onClose={() => setImporting(false)}
        onDone={(r) => { setImporting(false); onChanged('slug' in r ? r.slug : undefined) }} />}
    </div>
  )
}
