import { useEffect, useRef, useState, type ReactNode } from 'react'
import { ApiError } from '../../api'
import { useHotkey } from '../../keys'
import { useDialogFocus } from '../useDialogFocus'

// A confirmation with one error line. `onConfirm` runs the action: when it rejects, the dialog stays open and shows the
// message (a 409 refusal shows the backend's own); when it resolves, the caller has already closed or replaced the dialog.
// `danger` styles the confirm button as destructive (Force stop, Discard). `progress`, for an action that can take a while
// (Start waits for TRex to come up), replaces the body while it runs with a bar and an elapsed count against `limitS`.
export interface ConfirmProgress { label: string; busyLabel: string; limitS: number; detail?: ReactNode }
export function ConfirmDialog({ title, body, confirmLabel, cancelLabel = 'Cancel', danger = false, progress, onConfirm, onClose }: {
  title: string; body: ReactNode; confirmLabel: string; cancelLabel?: string; danger?: boolean; progress?: ConfirmProgress
  onConfirm: () => Promise<void>; onClose: () => void
}) {
  const ref = useDialogFocus<HTMLDivElement>('[data-autofocus]')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const inFlight = useRef(false)
  const [elapsed, setElapsed] = useState(0)
  const showProgress = busy && progress !== undefined
  // One tick a second while the progress view shows; confirm() resets the count to 0.
  useEffect(() => {
    if (!showProgress) return
    const started = Date.now()
    const t = setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000)
    return () => clearInterval(t)
  }, [showProgress])
  const close = () => { if (!inFlight.current) onClose() }
  useHotkey('escape', close)
  async function confirm() {
    if (inFlight.current) return
    inFlight.current = true; setElapsed(0); setBusy(true); setError(null)
    try { await onConfirm() }
    catch (x) { setError(x instanceof ApiError || x instanceof Error ? x.message : String(x)) }
    finally { inFlight.current = false; setBusy(false) }
  }
  return (
    <div className="modal confirm-dialog" role="dialog" aria-modal="true" aria-label={title} ref={ref}>
      <h2>{title}</h2>
      {showProgress
        ? <ProgressView progress={progress} elapsed={elapsed} />
        : <div>{body}</div>}
      {error && <p role="alert" className="error">{error}</p>}
      <div className="dialog-footer">
        <div className="end">
          <button type="button" disabled={busy} onClick={close}>{cancelLabel}</button>
          <button type="button" className={danger ? 'danger' : undefined} data-autofocus disabled={busy} onClick={() => void confirm()}>{showProgress ? progress.busyLabel : confirmLabel}</button>
        </div>
      </div>
    </div>
  )
}

const mss = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`

// The running action: a bar that fills over `limitS` (full, not overflowing, once past it) and the elapsed count. The count is
// a timer role (not announced each second); the label is announced once, when the view appears.
function ProgressView({ progress, elapsed }: { progress: ConfirmProgress; elapsed: number }) {
  return (
    <div className="confirm-progress">
      <p role="status">{progress.label}…</p>
      <progress aria-label={progress.label} max={progress.limitS} value={Math.min(elapsed, progress.limitS)} />
      <p role="timer" className="muted">{mss(elapsed)} of up to {mss(progress.limitS)}</p>
      {progress.detail}
    </div>
  )
}
