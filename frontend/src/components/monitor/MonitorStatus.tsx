import type { MonitorMsg } from '../../api'
import { usePulse, useMonitor } from '../../monitorStore'

const clock = (epochS: number) => new Date(epochS * 1000).toTimeString().slice(0, 8)   // HH:MM:SS, local time

// "Connected", "Connecting…", "Stale since HH:MM:SS" or "Disconnected".
function stateText(m: MonitorMsg | null): string {
  if (!m) return 'Disconnected'
  switch (m.state) {
    case 'connected': return 'Connected'
    case 'connecting': return 'Connecting…'
    case 'stale': return m.stale_since === null ? 'Stale' : `Stale since ${clock(m.stale_since)}`
    default: return 'Disconnected'
  }
}

// The counts the subscriber refused, only the ones that are not zero.
function dropText(m: MonitorMsg): string {
  const d = m.dropped
  const parts: [number, string][] = [[d.oversize, 'oversize'], [d.undecodable, 'undecodable'], [d.other_names, 'other names over the cap'],
    [d.long_name, 'name too long'], [m.dropped_triggers, 'triggers']]
  const shown = parts.filter(([n]) => n > 0).map(([n, what]) => `${n.toLocaleString('en-US')} ${what}`)
  return shown.length ? `dropped: ${shown.join(', ')}` : ''
}

// The source it watches, the LED, its text, then "frames · bytes" and the drop counts. The LED pulses (a 300 ms animation, restarted by its key)
// each time the frame count grows; the text carries the state, so colour is never the only signal.
export function MonitorStatus({ className }: { className?: string }) {
  const m = useMonitor((x) => x)
  const pulse = usePulse()
  const state = m?.state ?? 'disconnected'
  const drops = m ? dropText(m) : ''
  return (
    <p className={`monitor-status${className ? ` ${className}` : ''}`}>
      {/* names this page's own connection, so it is not read as the Control session's (the status bar) */}
      {m?.target && <span className="muted">Monitor source {m.target.host}:{m.target.port}</span>}
      <span key={state === 'connected' ? pulse : 'idle'} className={`led led-${state}${state === 'connected' ? ' led-pulse' : ''}`} aria-hidden="true" />
      {/* only the state is a live region: the counts change at 2 Hz */}
      <span role="status">{stateText(m)}</span>
      {m && m.frames > 0 && <span className="muted">{m.frames.toLocaleString('en-US')} frames · {m.bytes.toLocaleString('en-US')} bytes</span>}
      {drops && <span className="muted">{drops}</span>}
    </p>
  )
}
