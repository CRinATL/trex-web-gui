import { useState } from 'react'
import { createPortal } from 'react-dom'
import { NavLink } from 'react-router-dom'
import { connect, type SessionSnapshot } from '../api'
import { useHotkey } from '../keys'
import { useDialogFocus } from './useDialogFocus'

// The loss itself: what the backend found (the session's error, e.g. the TRex server process exited) and the ways on.
function Dialog({ session, onClose }: { session: SessionSnapshot; onClose: () => void }) {
  const ref = useDialogFocus<HTMLDivElement>('[data-autofocus]')
  useHotkey('escape', onClose)
  const params = session.params
  function reconnect() {
    onClose()
    // a failed reconnect leaves the session disconnected with its error, which the status bar shows
    if (params) connect(params).catch(() => {})
  }
  return createPortal(
    <div className="modal-backdrop">
      <div role="dialog" aria-modal="true" aria-labelledby="loss-title" aria-describedby="loss-cause" className="modal loss-dialog" ref={ref}>
        <h2 id="loss-title">Connection to TRex lost</h2>
        <p id="loss-cause">{session.error ?? 'The connection was lost for an unknown cause.'}</p>
        <p className="muted">The pages keep the last values received. The status bar keeps this message.</p>
        <div className="dialog-footer">
          {params && <button type="button" data-autofocus onClick={reconnect}>Reconnect</button>}
          <NavLink to="/events" onClick={onClose}>See Events</NavLink>
          <button type="button" data-autofocus={params ? undefined : true} onClick={onClose}>Close</button>
        </div>
      </div>
    </div>,
    document.body,
  )
}

// Opens once per loss of the TRex connection. A loss is keyed by the connection it ended (connect_seq): Close keeps
// it shut for that loss, and only a later connection's loss opens it again. A Disconnect is not a loss: the session
// goes disconnected, never lost.
export function LossDialog({ session }: { session: SessionSnapshot }) {
  const [closedFor, setClosedFor] = useState<string | null>(null)
  const key = session.connect_seq ?? ''
  if (session.state !== 'lost' || closedFor === key) return null
  return <Dialog session={session} onClose={() => setClosedFor(key)} />
}
