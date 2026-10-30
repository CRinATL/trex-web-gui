import { useRef, useState } from 'react'
import { ApiError, importProfile, inspect, uploadFile, type DiffReply, type ImportBody, type Inspected, type LibraryEntry, type PcapOptions } from '../../api'
import { useHotkey } from '../../keys'
import { defaultsFor, tunablesToArgs } from '../../traffic'
import { BrowseDialog } from '../BrowseDialog'
import { TunablesForm } from '../TunablesForm'
import { useDialogFocus } from '../useDialogFocus'
import { PCAP_DEFAULTS, pcapError } from './pcapOptions'
import { PcapWizard } from './PcapWizard'

type Kind = NonNullable<ImportBody['kind']>
// The import kinds by suffix (backend studio/convert.py IMPORT_KINDS); an upload takes all but .py (R4).
const KINDS: Record<string, Kind> = { '.py': 'py', '.yaml': 'yaml', '.yml': 'yaml', '.json': 'json', '.pcap': 'pcap', '.cap': 'pcap' }
const UPLOADS = '.yaml,.yml,.json,.pcap,.cap'
export const PY_UPLOAD_REFUSED = 'A .py profile must be on the backend host: the library keeps a link to it.'

const errText = (x: unknown) => (x instanceof ApiError || x instanceof Error ? x.message : String(x))
const baseName = (p: string) => p.replace(/\/+$/, '').split('/').pop() ?? ''
const suffixOf = (p: string) => /\.[^.]*$/.exec(baseName(p))?.[0].toLowerCase() ?? ''
const stemOf = (p: string) => { const b = baseName(p); const s = suffixOf(p); return s ? b.slice(0, b.length - s.length) : b }

// Import a file into a new library profile, or (`appendTo`) append a pcap's streams to one. The source is a path on the
// backend host or a file from this computer (uploaded first, R3); the kind comes from the suffix. A .py is inspected
// only when asked (layout rework §3), then its tunables are set; a pcap takes the wizard's options. A new profile's
// name defaults to the file's stem, which the body always carries (ruling Task 6). `onDone` gets the reply.
// An append is sent through `appendTo.send`, which the editor queues behind its other writes: it calls `build` with
// the rev current at that write's turn. `blocked`: an index-shifting write of the editor is pending, so Import waits.
// `active` false (the page is hidden): Escape does nothing.
export function ImportDialog({ appendTo, onDone, onClose, active = true, blocked = false }: {
  appendTo?: { slug: string; send: (build: (rev: string) => ImportBody) => Promise<DiffReply> }
  onDone: (reply: LibraryEntry | DiffReply) => void; onClose: () => void; active?: boolean; blocked?: boolean
}) {
  const ref = useDialogFocus<HTMLDivElement>('[data-autofocus]')
  const [where, setWhere] = useState<'host' | 'local'>('host')
  const [path, setPath] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [name, setName] = useState('')
  const [nameEdited, setNameEdited] = useState(false)
  const [inspected, setInspected] = useState<Inspected | null>(null)
  const [values, setValues] = useState<Record<string, unknown>>({})
  const [extra, setExtra] = useState('')
  const [pcap, setPcap] = useState<PcapOptions>(PCAP_DEFAULTS)
  const [browsing, setBrowsing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<{ status: number; text: string } | null>(null)
  // The upload of the picked file: a failed import keeps it on the backend, so a retry does not upload it again.
  const uploaded = useRef<{ file: File; id: string } | null>(null)
  // An open Browse dialog has its own Escape; this one must not close with it.
  useHotkey('escape', () => { if (active && !browsing) onClose() })

  const title = appendTo ? 'Import streams from pcap' : 'Import a profile'
  const picked = where === 'host' ? path.trim() : file?.name ?? ''
  const kind: Kind | null = KINDS[suffixOf(picked)] ?? null
  // A suffix that names no kind is said in the Kind row (the path may still be being typed); a known kind this
  // source or mode cannot take is the error line.
  const problem = kind === null ? null
    : appendTo && kind !== 'pcap' ? 'Only a .pcap or .cap file appends streams.'
    : where === 'local' && kind === 'py' ? PY_UPLOAD_REFUSED
    : null
  const suffixes = appendTo ? '.pcap .cap' : `${where === 'host' ? '.py ' : ''}.yaml .yml .json .pcap .cap`
  // the wizard shows its own error line, so its problem only disables Import
  const ready = !busy && !blocked && !!picked && kind !== null && problem === null && (kind !== 'py' || inspected !== null)
    && (kind !== 'pcap' || pcapError(pcap) === null) && (!!appendTo || name.trim() !== '')

  // A new source: a new default name (unless typed), and a .py must be inspected again.
  function picking(p: string) {
    setError(null); setInspected(null)
    if (!nameEdited) setName(stemOf(p))
  }
  function edited<T>(set: (v: T) => void) { return (v: T) => { setError(null); set(v) } }

  async function doInspect() {
    setBusy(true); setError(null)
    try {
      const i = await inspect({ path: path.trim() })
      setInspected(i); setValues(defaultsFor(i.tunables)); setExtra('')
    } catch (x) { setInspected(null); setError({ status: x instanceof ApiError ? x.status : 0, text: errText(x) }) }
    finally { setBusy(false) }
  }
  async function doImport() {
    if (!ready || kind === null) return
    setBusy(true); setError(null)
    try {
      let source: Pick<ImportBody, 'path' | 'upload_id'>
      if (where === 'host') source = { path: path.trim() }
      else {
        if (!file) return
        if (uploaded.current?.file !== file) uploaded.current = { file, id: (await uploadFile(file)).upload_id }
        source = { upload_id: uploaded.current.id }
      }
      const body: ImportBody = {
        ...source, kind,
        ...(kind === 'py' && inspected ? { tunables: tunablesToArgs(inspected.tunables, values, extra), tunable_form: { values, extra } } : {}),
        ...(kind === 'pcap' ? { pcap } : {}),
        ...(appendTo ? {} : { name: name.trim() }),
      }
      onDone(appendTo ? await appendTo.send((rev) => ({ ...body, append_to: { slug: appendTo.slug, rev } })) : await importProfile(body))
    } catch (x) {
      // an expired upload is gone from the backend: the next attempt uploads the file again
      if (x instanceof ApiError && x.status === 404) uploaded.current = null
      setError({ status: x instanceof ApiError ? x.status : 0, text: errText(x) }) }
    finally { setBusy(false) }
  }

  const shown = problem !== null ? { status: 0, text: problem } : error
  return (
    <div role="dialog" aria-modal="true" aria-label={title} className="modal import-dialog" ref={ref}>
      <h2>{title}</h2>
      <div className="form-grid">
        <div className="form-row">
          <span className="row-label">Source</span>
          <span role="radiogroup" aria-label="Source">
            <label><input type="radio" name="import-source" data-autofocus checked={where === 'host'}
              onChange={() => { setWhere('host'); picking(path) }} /> On the backend host</label>{' '}
            <label><input type="radio" name="import-source" checked={where === 'local'}
              onChange={() => { setWhere('local'); picking(file?.name ?? '') }} /> From this computer</label>
          </span>
        </div>
        {where === 'host' ? (
          <div className="form-row">
            <label htmlFor="import-path">Path</label>
            <span>
              <input id="import-path" type="text" value={path} onChange={(e) => { setPath(e.target.value); picking(e.target.value) }} />{' '}
              <button type="button" disabled={busy} onClick={() => setBrowsing(true)}>Browse…</button>
            </span>
          </div>
        ) : (
          <div className="form-row">
            <label htmlFor="import-file">File</label>
            <input id="import-file" type="file" accept={appendTo ? '.pcap,.cap' : UPLOADS}
              onChange={(e) => { const f = e.target.files?.[0] ?? null; setFile(f); picking(f?.name ?? '') }} />
          </div>
        )}
        <div className="form-row">
          <span className="row-label">Kind</span>
          <span>{kind ?? <span className="muted">{picked ? `none: only ${suffixes} files import` : 'from the suffix'}</span>}</span>
        </div>
        {kind === 'py' && where === 'host' && (
          <div className="form-row">
            <span className="row-label">Tunables</span>
            <span>
              <button type="button" disabled={busy || !picked} onClick={() => void doInspect()}>Inspect</button>{' '}
              {!inspected && <span className="muted">Inspect runs the profile to read its tunables.</span>}
            </span>
          </div>
        )}
        {kind === 'py' && inspected && (
          <div className="full"><TunablesForm specs={inspected.tunables} values={values} extra={extra} onValues={edited(setValues)} onExtra={edited(setExtra)} /></div>
        )}
        {kind === 'pcap' && <div className="full"><PcapWizard value={pcap} onChange={edited(setPcap)} /></div>}
        {!appendTo && (
          <div className="form-row">
            <label htmlFor="import-name">Name</label>
            <input id="import-name" type="text" value={name} onChange={(e) => { setError(null); setName(e.target.value); setNameEdited(true) }} />
          </div>
        )}
      </div>
      {shown && (shown.status === 422
        ? <pre role="alert" className="error profile-error">{shown.text}</pre>
        : <p role="alert" className="error">{shown.text}</p>)}
      <div className="dialog-footer">
        {blocked && !busy && <span className="muted">Waiting for the table's last change to be saved and read back.</span>}
        <div className="end">
          <button type="button" onClick={onClose}>Cancel</button>
          <button type="button" disabled={!ready} onClick={() => void doImport()}>Import</button>
        </div>
      </div>
      {browsing && <BrowseDialog title="Browse for a file to import" pcap active={active}
        onClose={() => setBrowsing(false)} onPick={(p) => { setBrowsing(false); setPath(p); picking(p) }} />}
    </div>
  )
}
