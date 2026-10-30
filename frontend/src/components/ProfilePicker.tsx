import { useRef, useState, type Ref } from 'react'
import { ApiError, inspect, type Inspected, type LibraryEntry } from '../api'
import { Info } from '../ui/Info'
import { useHost } from '../useHost'
import { BrowseDialog } from './BrowseDialog'
import { LibraryPickDialog } from './studio/LibraryPickDialog'
import { pySourceOf } from './studio/libraryPick'

// The data profiles (backend/trex_web_gui/trexapi/profiles.py:13): picking one inspects it at once; a .py only fills the path.
const INSPECTABLE = /\.(ya?ml|json)$/i
const errText = (x: unknown) => (x instanceof Error ? x.message : String(x))

export function ProfilePicker({ portId, path, onPath, onInspected, inputRef, disabled, tunablesFor, visible = true, onLibraryPick }: {
  portId: number; path: string; onPath: (p: string) => void; onInspected: (i: Inspected | null) => void
  inputRef?: Ref<HTMLInputElement>; disabled?: boolean
  // the tunable arguments to inspect with; empty for a path that has not been inspected yet
  tunablesFor?: (path: string) => string[]
  visible?: boolean   // false while the tab or page is hidden: an open Browse dialog must not keep its Escape hotkey
  // a .py library entry: the caller fills the path and the entry's tunables (a JSON-backed entry is picked and inspected here)
  onLibraryPick?: (e: LibraryEntry) => void
}) {
  const host = useHost()
  const [browsing, setBrowsing] = useState(false)
  const [library, setLibrary] = useState(false)
  if (!visible && (browsing || library)) { setBrowsing(false); setLibrary(false) }   // adjusting state during render, as ControlPage does
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<{ status: number; message: string } | null>(null)
  const inFlight = useRef(false)

  async function doInspect(p: string) {
    if (inFlight.current || !p.trim()) return
    inFlight.current = true; setBusy(true); setError(null)
    try { onInspected(await inspect({ path: p.trim(), ports: [portId], tunables: tunablesFor?.(p.trim()) ?? [] })) }
    catch (x) { onInspected(null); setError({ status: x instanceof ApiError ? x.status : 0, message: errText(x) }) }
    finally { inFlight.current = false; setBusy(false) }
  }

  function pickLibrary(e: LibraryEntry) {
    setLibrary(false)
    const src = pySourceOf(e)
    if (src) { if (onLibraryPick) onLibraryPick(e); else onPath(src) }
    else { onPath(e.path); void doInspect(e.path) }
  }

  return (
    <div className="profile-picker">
      <label>Profile path <input ref={inputRef} type="text" value={path} disabled={disabled} onChange={(e) => onPath(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') void doInspect(path) }} /></label>
      <button type="button" disabled={disabled} onClick={() => setBrowsing(true)}>Browse…</button>
      <button type="button" disabled={disabled} onClick={() => setLibrary(true)}>Library…</button>
      <button type="button" disabled={disabled || busy || !path.trim()} onClick={() => void doInspect(path)}>Inspect</button>
      <Info label="profile paths">
        <p>Paths are on {host ? `${host}, ` : ''}the computer running trex-web-gui serve, not the one your browser is on. Nothing is uploaded.</p>
        <p>.py profiles run as code {host ? `on ${host}` : 'there'}.</p>
      </Info>
      {error && (error.status === 422
        ? <pre role="alert" className="error profile-error">{error.message}</pre>
        : <p role="alert" className="error">{error.message}</p>)}
      {library && <LibraryPickDialog active={visible} onClose={() => setLibrary(false)} onPick={pickLibrary} />}
      {browsing && <BrowseDialog onClose={() => setBrowsing(false)} onPick={(p) => { setBrowsing(false); onPath(p); if (INSPECTABLE.test(p)) void doInspect(p) }} />}
    </div>
  )
}
