import type { EmuMsg } from '../../api'
import { useEmu } from '../../emuStore'

// "Connected", "Connecting…", "Connection lost" or "Disconnected".
function stateText(m: EmuMsg | null): string {
  switch (m?.state) {
    case 'connected': return 'Connected'
    case 'connecting': return 'Connecting…'
    case 'lost': return 'Connection lost'
    default: return 'Disconnected'
  }
}

// The LED and state text, then the server's version and the generation, the busy progress and the cause of a loss. The text carries
// the state, so colour is never the only signal.
export function EmuStatus() {
  const m = useEmu((x) => x)
  const state = m?.state ?? 'disconnected'
  const ledState = state === 'lost' ? 'stale' : state
  return (
    <p className="monitor-status">
      {m?.target && <span className="muted">EMU server {m.target.host}:{m.target.port}</span>}
      <span className={`led led-${ledState}`} aria-hidden="true" />
      <span role="status">{stateText(m)}</span>
      {m?.info && <span className="muted">version {m.info.version} · generation {m.generation}</span>}
      {m?.busy && <span className="muted">{m.busy.kind}: {m.busy.done} of {m.busy.total}</span>}
      {m?.state === 'lost' && m.error && <span role="alert" className="error">{m.error}</span>}
    </p>
  )
}
