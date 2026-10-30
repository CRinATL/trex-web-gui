import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, deleteUserTemplate, listTemplates, saveUserTemplate, serverTemplate, userTemplate, type PacketDoc, type TemplateList } from '../../api'
import { useHotkey } from '../../keys'
import { useDialogFocus } from '../useDialogFocus'

const errText = (x: unknown) => (x instanceof Error ? x.message : String(x))

// Templates by their `/`-separated names: a group per leading segment, the templates inside it.
interface Leaf { label: string; name: string; buildable: boolean; reason: string | null }
interface Group { folders: Map<string, Group>; leaves: Leaf[] }
function treeOf(items: { name: string; buildable: boolean; reason: string | null }[]): Group {
  const root: Group = { folders: new Map(), leaves: [] }
  for (const it of items) {
    const parts = it.name.split('/')
    let g = root
    for (const p of parts.slice(0, -1)) {
      if (!g.folders.has(p)) g.folders.set(p, { folders: new Map(), leaves: [] })
      g = g.folders.get(p)!
    }
    g.leaves.push({ ...it, label: parts[parts.length - 1] })
  }
  return root
}

function Tree({ group, open, onDelete }: { group: Group; open: (name: string) => void; onDelete?: (name: string) => void }) {
  return (
    <ul className="template-tree">
      {[...group.folders].map(([name, g]) => (
        <li key={`d:${name}`}><details open><summary>{name}/</summary><Tree group={g} open={open} onDelete={onDelete} /></details></li>
      ))}
      {group.leaves.map((l) => (
        <li key={l.name}>
          <button type="button" disabled={!l.buildable} title={l.reason ?? undefined} onClick={() => open(l.name)}>{l.label}</button>
          {!l.buildable && l.reason && <span className="muted"> {l.reason}</span>}
          {onDelete && <> <button type="button" aria-label={`Delete ${l.name}`} onClick={() => onDelete(l.name)}>Delete</button></>}
        </li>
      ))}
    </ul>
  )
}

// The templates dialog (B4.1, B4.2): the server's templates and the user's, each a tree by `/`, and, when `doc` is given, a
// row that saves it as a user template. A server template is labelled by its path, never by the name in its file (several
// of those are wrong). `onOpen` gets the template's document and its name.
export function Templates({ doc, onOpen, onClose, active = true }: {
  doc?: PacketDoc; onOpen: (doc: PacketDoc, name: string) => void; onClose: () => void; active?: boolean
}) {
  const ref = useDialogFocus<HTMLDivElement>('[data-autofocus]')
  const [list, setList] = useState<TemplateList | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [replace, setReplace] = useState<string | null>(null)   // the name a 409 asked about
  const [deleting, setDeleting] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const live = useRef(true)
  useEffect(() => { live.current = true; return () => { live.current = false } }, [])
  useHotkey('escape', () => { if (active) onClose() })

  const load = useCallback(async () => {
    try { const l = await listTemplates(); if (live.current) { setList(l); setError(null) } } catch (x) { if (live.current) setError(errText(x)) }
  }, [])
  useEffect(() => { const t = setTimeout(() => void load(), 0); return () => clearTimeout(t) }, [load])

  async function open(kind: 'server' | 'user', n: string) {
    setError(null)
    try {
      const r = await (kind === 'server' ? serverTemplate(n) : userTemplate(n))
      if (live.current) onOpen(r.doc, n)
    } catch (x) { if (live.current) setError(errText(x)) }
  }
  async function save(overwrite: boolean) {
    if (!doc) return
    const n = name.trim()
    setBusy(true); setError(null); setStatus(null)
    try {
      await saveUserTemplate(n, doc, overwrite)
      if (!live.current) return
      setReplace(null); setStatus(`Saved ${n}`)
      await load()
    } catch (x) {
      if (!live.current) return
      if (x instanceof ApiError && x.status === 409 && x.code === 'exists') setReplace(n)
      else setError(errText(x))
    } finally { if (live.current) setBusy(false) }
  }
  async function remove(n: string) {
    setDeleting(null); setError(null); setStatus(null)
    try { await deleteUserTemplate(n); if (live.current) { setStatus(`Deleted ${n}`); await load() } } catch (x) { if (live.current) setError(errText(x)) }
  }

  return (
    <div role="dialog" aria-modal="true" aria-label="Templates" className="modal packet-dialog" ref={ref}>
      <h2>Templates</h2>
      {error && <p role="alert" className="error">{error}</p>}
      {status && <p role="status" className="muted">{status}</p>}
      {list === null && !error && <p className="muted">Loading…</p>}
      {list && (
        <div className="template-lists">
          <h3>Server templates</h3>
          <Tree group={treeOf(list.server.map((s) => ({ name: s.id, buildable: s.buildable, reason: s.reason })))} open={(n) => void open('server', n)} />
          <h3>Your templates</h3>
          {list.user.length === 0 && <p className="muted">None yet. Save the packet as a template to keep it here{list.dir ? ` (${list.dir})` : ''}.</p>}
          {list.user.length > 0 && <Tree group={treeOf(list.user.map((u) => ({ name: u, buildable: true, reason: null })))} open={(n) => void open('user', n)} onDelete={setDeleting} />}
          {deleting !== null && (
            <p role="alert">Delete {deleting}? <button type="button" onClick={() => void remove(deleting)}>Delete</button> <button type="button" onClick={() => setDeleting(null)}>Keep</button></p>
          )}
        </div>
      )}
      {doc && (
        <form className="template-save" onSubmit={(e) => { e.preventDefault(); if (name.trim() !== '') void save(false) }}>
          <label>Save the packet as a template <input type="text" value={name} spellCheck={false} placeholder="lab/syn-flood" onChange={(e) => { setName(e.target.value); setReplace(null) }} /></label>{' '}
          <button type="submit" disabled={busy || name.trim() === ''}>Save</button>
          {replace !== null && (
            <p role="alert">Replace {replace}? <button type="button" disabled={busy} onClick={() => void save(true)}>Replace</button> <button type="button" onClick={() => setReplace(null)}>Cancel</button></p>
          )}
        </form>
      )}
      <div className="dialog-footer"><div className="end"><button type="button" data-autofocus onClick={onClose}>Close</button></div></div>
    </div>
  )
}
