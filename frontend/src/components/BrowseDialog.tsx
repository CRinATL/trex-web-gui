import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { fsList, type FsList } from '../api'
import { useHotkey } from '../keys'
import { useDialogFocus } from './useDialogFocus'
import { sizeText } from './sizeText'

const DIR_KEY = 'trex-web-gui.profileDir'
const errText = (x: unknown) => (x instanceof Error ? x.message : String(x))
const rememberedDir = (): string | undefined => { try { return localStorage.getItem(DIR_KEY) ?? undefined } catch { return undefined } }
const remember = (dir: string) => { try { localStorage.setItem(DIR_KEY, dir) } catch { /* storage unavailable: the browser just opens at the default */ } }

// A remembered directory that has since vanished must not trap the user: retry once at the backend's default.
async function listWithFallback(list: (dir?: string) => Promise<FsList>, dir: string | undefined): Promise<FsList> {
  if (dir === undefined) return list()
  try { return await list(dir) } catch { return list() }
}

// The listing call: `pcap` also asks for .pcap and .cap files.
const lister = (pcap: boolean) => (dir?: string): Promise<FsList> => (pcap ? fsList(dir, { pcap: true }) : dir === undefined ? fsList() : fsList(dir))

// The backend host's file browser: directories enter, Up goes to the parent, a file picks its full path. The last
// directory shown is remembered for the next Browse. `pcap` also lists .pcap and .cap files (the Studio's import).
// `active` false (its page is hidden): Escape does nothing.
export function BrowseDialog({ onPick, onClose, title = 'Browse for a profile', pcap = false, active = true }: {
  onPick: (path: string) => void; onClose: () => void; title?: string; pcap?: boolean; active?: boolean
}) {
  const ref = useDialogFocus<HTMLDivElement>('[data-autofocus]')
  const [listing, setListing] = useState<FsList | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(true)   // the first listing is loading
  useHotkey('escape', () => { if (active) onClose() })

  const show = (l: FsList) => { setListing(l); remember(l.path) }
  // dir === undefined opens the remembered directory, falling back to the backend's default
  useEffect(() => {
    let live = true
    listWithFallback(lister(pcap), rememberedDir())
      .then((l) => { if (live) show(l) })
      .catch((x) => { if (live) setError(errText(x)) })
      .finally(() => { if (live) setBusy(false) })
    return () => { live = false }
  }, [pcap])
  async function go(dir: string) {
    setBusy(true); setError(null)
    try { show(await lister(pcap)(dir)) } catch (x) { setError(errText(x)) } finally { setBusy(false) }
  }

  const join = (dir: string, name: string) => `${dir.replace(/\/$/, '')}/${name}`
  const [filter, setFilter] = useState('')
  const all = listing?.entries ?? []
  const needle = filter.trim().toLowerCase()
  const shown = needle ? all.filter((e) => e.name.toLowerCase().includes(needle)) : all
  async function enter(dir: string) { setFilter(''); await go(dir) }
  // Rendered into <body>: a dialog that opens it (the pcap picker, Import, the daemon's YAML field) is itself a centred,
  // transformed and scrolling box, and a fixed child of a transformed box is placed and clipped inside it.
  return createPortal(
    <div className="modal-backdrop">
      <div role="dialog" aria-modal="true" aria-label={title} className="modal browse" ref={ref}>
        <h2>{title}</h2>
        <div className="browse-where">
          <button type="button" disabled={busy || !listing?.parent} onClick={() => listing?.parent && void enter(listing.parent)}>Up</button>
          <p className="path" aria-live="polite">{listing?.path ?? ''}</p>
        </div>
        <p className="browse-filter">
          {/* a plain text field: Escape on a search field clears it natively and would also close the dialog */}
          <label>Filter <input type="text" value={filter} spellCheck={false} data-autofocus onChange={(e) => setFilter(e.target.value)} /></label>
          <span className="muted" aria-live="polite">{needle && ` ${shown.length} of ${all.length}`}</span>
        </p>
        {error && <p role="alert" className="error">{error}</p>}
        <div className="browse-list">
          <ul>
            {shown.map((e) => (
              <li key={e.name}>
                <button type="button" disabled={busy} aria-label={e.dir ? `${e.name}/` : e.name} onClick={() => (e.dir ? void enter(join(listing!.path, e.name)) : onPick(join(listing!.path, e.name)))}>
                  <span className={e.dir ? 'browse-name browse-dir' : 'browse-name'}>{e.dir ? `${e.name}/` : e.name}</span>
                  {!e.dir && e.size !== null && <span className="muted browse-size">{sizeText(e.size)}</span>}
                </button>
              </li>
            ))}
          </ul>
          {listing && all.length > 0 && shown.length === 0 && <p className="muted">Nothing here matches the filter.</p>}
          {listing && all.length === 0 && <p className="muted">{pcap ? 'No profiles, pcaps or directories here.' : 'No profiles or directories here.'}</p>}
        </div>
        {listing?.truncated && <p className="muted">The list was truncated; type the path instead.</p>}
        <div className="dialog-footer"><div className="end"><button type="button" onClick={onClose}>Cancel</button></div></div>
      </div>
    </div>,
    document.body,
  )
}
