import { useRef, useState } from 'react'
import { NavLink } from 'react-router-dom'
import { ApiError, connect, disconnect, type SessionSnapshot } from '../api'

const OWNED_LIST_MAX = 4   // more ports than this are shown as a count

// "you own 0, 1" for a few ports, "you own 12 of 16" (or "12 ports" without the total) for many.
function ownedText(owned: number[], total: number | undefined): string {
  if (owned.length <= OWNED_LIST_MAX) return `you own ${owned.join(', ')}`
  return total === undefined ? `you own ${owned.length} ports` : `you own ${owned.length} of ${total}`
}

const failText = (x: unknown) => (x instanceof ApiError ? x.message : String(x))

// The connection state and, at the right, the actions that change it. A failed Reconnect leaves the session disconnected with
// the error (the backend does not keep it lost), and reads and acts like lost. Connect… is the page-independent entry (the dialog is the app's).
export function StatusBar({ backendOnline, session, owned, onConnect }: { backendOnline: boolean; session: SessionSnapshot; owned: number[]; onConnect: () => void }) {
  const [failure, setFailure] = useState<string | null>(null)
  // A failed Disconnect or Reconnect stays until the next attempt or any change of session state; adjusting state during render, as ControlPage does.
  const [seenState, setSeenState] = useState(session.state)
  if (session.state !== seenState) { setSeenState(session.state); setFailure(null) }
  // One request per button: each button is disabled only while its own request is in flight (the refs also stop a second
  // click before the re-render). A Disconnect sent during a Reconnect waits on the backend's session lock and then
  // disconnects, so neither action's end clears the other's flag.
  const [disconnecting, setDisconnecting] = useState(false)
  const [reconnecting, setReconnecting] = useState(false)
  const disconnectInFlight = useRef(false)
  const reconnectInFlight = useRef(false)
  async function attempt(flight: { current: boolean }, setBusy: (b: boolean) => void, action: () => Promise<unknown>) {
    if (flight.current) return
    flight.current = true; setBusy(true); setFailure(null)
    try { await action() } catch (x) { setFailure(failText(x)) } finally { flight.current = false; setBusy(false) }
  }
  let text: string
  if (!backendOnline) text = 'Backend offline, reconnecting…'
  else if (session.state === 'connected') {
    // params/info can be null in a transient snapshot; say "Connected" with whatever is known
    const parts = [session.params ? `Connected to ${session.params.host}:${session.params.sync_port}` : 'Connected']
    if (session.info) parts.push(`TRex ${session.info.version}`, `${session.info.port_count} ports`)
    if (owned.length > 0) parts.push(ownedText(owned, session.info?.port_count))
    if (session.params?.read_only) parts.push('read-only')
    text = parts.join(' · ')
  }
  else if (session.state === 'lost') text = `Connection lost: ${session.error ?? 'unknown cause'}`
  else if (session.state === 'connecting') text = 'Connecting…'
  else if (session.state === 'disconnected' && session.error) text = `Disconnected: ${session.error}`
  else text = 'Disconnected'
  const connectButton = <button type="button" data-run="go" disabled={!backendOnline} onClick={onConnect}>Connect…</button>
  let buttons: React.ReactNode
  if (!backendOnline) buttons = connectButton
  else if (session.state === 'connected' || session.state === 'connecting') buttons = <button type="button" disabled={disconnecting} onClick={() => void attempt(disconnectInFlight, setDisconnecting, disconnect)}>Disconnect</button>
  else if (session.state === 'disconnected' && !session.error) buttons = connectButton
  else {
    // lost, or disconnected with an error: only a failed or cancelled connect leaves one (a user Disconnect clears it),
    // so the lost session's way back is offered in both
    const params = session.params
    buttons = (
      <>
        <NavLink to="/events">See Events</NavLink>
        {params && <button type="button" disabled={reconnecting} onClick={() => void attempt(reconnectInFlight, setReconnecting, () => connect(params))}>Reconnect</button>}
        {connectButton}
      </>
    )
  }
  return (
    <footer className={`statusbar state-${backendOnline ? session.state : 'offline'}`}>
      <span role="status">{text}</span>
      {failure && <span role="alert" className="error">{failure}</span>}
      <span className="end">{buttons}</span>
    </footer>
  )
}
