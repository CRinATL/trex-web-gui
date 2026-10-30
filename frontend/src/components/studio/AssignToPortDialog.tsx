import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, type LibraryEntry, type PortView } from '../../api'
import { useHotkey } from '../../keys'
import { rateText } from '../../trafficStart'
import { trafficStore } from '../../trafficStore'
import { useDialogFocus } from '../useDialogFocus'
import { fillFromJson, fillFromPy, PY_NOTE, pySourceOf } from './libraryPick'

const errText = (x: unknown) => (x instanceof ApiError || x instanceof Error ? x.message : String(x))

// "Use in Control": writes a library entry into one port's Traffic form and opens that form in Control. Nothing is assigned
// here: the user presses Assign on the Traffic tab. The form keeps its own rate, which the dialog names. `active` false (the page is hidden): Escape does nothing.
export function AssignToPortDialog({ entry, ports, onClose, active = true }: { entry: LibraryEntry; ports: PortView[]; onClose: () => void; active?: boolean }) {
  const ref = useDialogFocus<HTMLDivElement>('[data-autofocus]')
  const navigate = useNavigate()
  const [port, setPort] = useState<number | null>(ports[0]?.id ?? null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useHotkey('escape', () => { if (active) onClose() })
  const src = pySourceOf(entry)
  const kept = port === null ? null : trafficStore.get(port)   // the form's rate, which the handoff does not change

  async function assign() {
    if (port === null) return
    setBusy(true); setError(null)
    try {
      if (src) fillFromPy(port, entry, src)
      else await fillFromJson(port, entry)
      navigate('/control', { state: { studioAssign: { port } } })
      onClose()
    } catch (x) { setError(errText(x)) }
    finally { setBusy(false) }
  }
  return (
    <div role="dialog" aria-modal="true" aria-label="Use in Control" className="modal" ref={ref}>
      <h2>Use in Control</h2>
      <div className="form-grid">
        <div className="form-row">
          <label htmlFor="assign-port">Port</label>
          <select id="assign-port" data-autofocus value={port ?? ''} disabled={ports.length === 0 || busy} onChange={(e) => setPort(Number(e.target.value))}>
            {ports.map((p) => <option key={p.id} value={p.id}>Port {p.id}</option>)}
          </select>
        </div>
        {ports.length === 0 && <p className="muted full">No ports: connect to a TRex server in Control first.</p>}
        <p className="muted full">Fills the profile field of that port's Traffic tab; nothing is assigned until you press Assign there.</p>
        {kept && <p className="muted full">Port {port}'s rate stays {rateText(kept)}{kept.duration.trim() ? `, ${kept.duration.trim()} s` : ''}{kept.force ? ', force' : ''}: check it on the Traffic tab before you press Assign.</p>}
        {src && <p className="muted full">{PY_NOTE}</p>}
        {error && <p role="alert" className="error full">{error}</p>}
      </div>
      <div className="dialog-footer">
        <div className="end">
          <button type="button" onClick={onClose}>Cancel</button>
          <button type="button" disabled={busy || port === null} onClick={() => void assign()}>Open Traffic tab</button>
        </div>
      </div>
    </div>
  )
}
