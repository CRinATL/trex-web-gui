import { useRef, useState, type ReactNode } from 'react'
import { ApiError } from '../../api'
import { useHotkey } from '../../keys'
import { useDialogFocus } from '../useDialogFocus'

// A confirmation with one error line. `onConfirm` runs the action: when it rejects, the dialog stays open and shows the
// message (a 409 refusal shows the backend's own); when it resolves, the caller has already closed or replaced the dialog.
// `danger` styles the confirm button as destructive (Force stop, Discard).
export function ConfirmDialog({ title, body, confirmLabel, cancelLabel = 'Cancel', danger = false, onConfirm, onClose }: {
  title: string; body: ReactNode; confirmLabel: string; cancelLabel?: string; danger?: boolean; onConfirm: () => Promise<void>; onClose: () => void
}) {
  const ref = useDialogFocus<HTMLDivElement>('[data-autofocus]')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const inFlight = useRef(false)
  const close = () => { if (!inFlight.current) onClose() }
  useHotkey('escape', close)
  async function confirm() {
    if (inFlight.current) return
    inFlight.current = true; setBusy(true); setError(null)
    try { await onConfirm() }
    catch (x) { setError(x instanceof ApiError || x instanceof Error ? x.message : String(x)) }
    finally { inFlight.current = false; setBusy(false) }
  }
  return (
    <div className="modal confirm-dialog" role="dialog" aria-modal="true" aria-label={title} ref={ref}>
      <h2>{title}</h2>
      <div>{body}</div>
      {error && <p role="alert" className="error">{error}</p>}
      <div className="dialog-footer">
        <div className="end">
          <button type="button" disabled={busy} onClick={close}>{cancelLabel}</button>
          <button type="button" className={danger ? 'danger' : undefined} data-autofocus disabled={busy} onClick={() => void confirm()}>{confirmLabel}</button>
        </div>
      </div>
    </div>
  )
}
