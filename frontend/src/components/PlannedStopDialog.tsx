import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { NavLink } from 'react-router-dom'
import { connect, type Ended, type Recovery as RecoveryInfo, type SessionSnapshot } from '../api'
import { useHotkey } from '../keys'
import { useDialogFocus } from './useDialogFocus'

const timeOf = (epoch: number) => new Date(epoch * 1000).toLocaleTimeString()

// Whole seconds left to an epoch deadline, re-read every second; never below zero.
function useSecondsLeft(deadline: number | null): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (deadline === null) return
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [deadline])
  return deadline === null ? 0 : Math.max(0, Math.ceil(deadline - now / 1000))
}

// Remounts the recovery line when the kind or the deadline changes, so its countdown starts from a fresh clock reading.
const recoveryKey = (r: RecoveryInfo) => (r.kind === 'reconnecting' ? `${r.kind}:${r.deadline}` : r.kind)

// The recovery line for the stop's kind, with its action where the spec gives one.
function Recovery({ ended, session, onClose }: { ended: Ended; session: SessionSnapshot; onClose: () => void }) {
  const rec = ended.recovery
  const left = useSecondsLeft(rec.kind === 'reconnecting' ? rec.deadline : null)
  const params = session.params
  switch (rec.kind) {
    case 'none': return <p>Nothing restarts it. Start TRex from the TRex daemon dialog to reconnect.</p>
    case 'reconnecting': return <p>TRex is starting. Control reconnects by itself, within {left} s.</p>
    case 'stf_run': return (
      <p>A stateful run has no command port. It ends at {timeOf(rec.ends_at)}; then nothing runs until TRex is started again.{' '}
        <NavLink to="/monitor" onClick={onClose}>Open Monitor</NavLink></p>
    )
    case 'reconnect_failed': return (
      <>
        <p id="planned-stop-error" className="error">{rec.error}</p>
        {params && <button type="button" data-autofocus onClick={() => { onClose(); connect(params).catch(() => {}) }}>Reconnect</button>}
      </>
    )
  }
}

// The dialog body: what the user did, what Control is now, and the way on.
function Dialog({ session, ended, onClose }: { session: SessionSnapshot; ended: Ended; onClose: () => void }) {
  const ref = useDialogFocus<HTMLDivElement>('[data-autofocus]')
  useHotkey('escape', onClose)
  const offersReconnect = ended.recovery.kind === 'reconnect_failed' && !!session.params
  return createPortal(
    <div className="modal-backdrop">
      <div role="dialog" aria-modal="true" aria-labelledby="planned-stop-title" aria-describedby="planned-stop-cause" className="modal planned-stop" ref={ref}>
        <h2 id="planned-stop-title">TRex stopped</h2>
        <p id="planned-stop-cause">You stopped TRex on {ended.daemon} from the TRex daemon dialog at {timeOf(ended.at)}.</p>
        <p>Control is disconnected.</p>
        <Recovery key={recoveryKey(ended.recovery)} ended={ended} session={session} onClose={onClose} />
        <div className="dialog-footer">
          {/* Reconnect holds the focus when it is offered; Close otherwise */}
          <button type="button" data-autofocus={offersReconnect ? undefined : true} onClick={onClose}>Close</button>
        </div>
      </div>
    </div>,
    document.body,
  )
}

// Opens once per planned stop, keyed by `ended.at`: Close keeps it shut for that stop, a new stop opens it again, and a
// new connection alone (connect_seq) does not. It closes itself when `ended` clears (Control reconnected). The loss
// dialog stays silent for the same stop.
export function PlannedStopDialog({ session }: { session: SessionSnapshot }) {
  const [closedFor, setClosedFor] = useState<number | null>(null)
  const ended = session.ended
  if (!ended || closedFor === ended.at) return null
  return <Dialog session={session} ended={ended} onClose={() => setClosedFor(ended.at)} />
}
