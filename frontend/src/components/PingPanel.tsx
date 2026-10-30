import { useRef, useState } from 'react'
import type { PortView } from '../api'
import { layerView } from '../layerView'
import { errText, isIpv4, isIpv6 } from '../portGate'
import { useBusy, usePingLog, usePingRunner } from '../state'

const int = (s: string) => (/^\d+$/.test(s.trim()) ? Number(s.trim()) : NaN)

// Ping from one port. `why` is the reason the port's P3b commands are blocked right now (PortToolsTab computes it once), or null.
export function PingPanel({ port, why }: { port: PortView; why: string | null }) {
  const id = port.id
  const run = usePingRunner()
  const busyKind = useBusy()[id]
  const log = usePingLog(id)
  const [dst, setDst] = useState('')
  const [count, setCount] = useState('5')
  const [size, setSize] = useState('64')
  const [errors, setErrors] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [restoreError, setRestoreError] = useState<string | null>(null)
  // Only guards a double click until the backend's busy message arrives; the busy state itself is the backend's.
  const inFlight = useRef(false)
  const [sending, setSending] = useState(false)

  // the newest run's lines: several runs sit one after another in the log
  const newest = log.reduce((m, l) => Math.max(m, l.run), 0)
  const lines = log.filter((l) => l.run === newest)
  const finished = lines.length > 0 && busyKind !== 'ping' && !sending

  async function send() {
    const d = dst.trim(), n = int(count), sz = int(size)
    const problems: string[] = []
    if (!isIpv4(d) && !isIpv6(d)) problems.push('The destination must be an IPv4 or IPv6 address.')
    else if (isIpv4(d) && layerView(port).arp !== 'resolved') problems.push('ARP resolution required: configure L3 IPv4 first.')
    if (!(n >= 1 && n <= 10)) problems.push('The count must be a number from 1 to 10.')
    if (!(sz >= 64 && sz <= 9216)) problems.push('The size must be a number from 64 to 9216.')
    setErrors(problems); setError(null); setRestoreError(null)
    if (problems.length || inFlight.current) return
    inFlight.current = true; setSending(true)
    try {
      const r = await run(id, d, n, sz)
      if (r.service_restore_error) setRestoreError(r.service_restore_error)
    } catch (x) { setError(errText(x)) }
    finally { inFlight.current = false; setSending(false) }
  }

  const blocked = why !== null || sending
  const received = lines.filter((l) => l.status === 'reply').length
  const sent = lines.filter((l) => l.status !== 'refused' && l.status !== 'error').length   // those two were never sent
  return (
    <div className="ping-panel">
      <label>Destination <input type="text" value={dst} disabled={blocked} title={why ?? undefined} onChange={(e) => setDst(e.target.value)} /></label>
      <label>Count <input type="text" inputMode="numeric" value={count} disabled={blocked} title={why ?? undefined} onChange={(e) => setCount(e.target.value)} /></label>
      <label>Size <input type="text" inputMode="numeric" value={size} disabled={blocked} title={why ?? undefined} onChange={(e) => setSize(e.target.value)} /></label>
      <button type="button" disabled={blocked} title={why ?? undefined} onClick={() => void send()}>Ping</button>
      <span className="muted"> Ping and Scan put the port in service mode while they run.</span>
      {errors.map((e) => <p key={e} role="alert" className="error">{e}</p>)}
      {error && <p role="alert" className="error">{error}</p>}
      {restoreError && <p role="alert" className="error">Service mode could not be restored: {restoreError}</p>}
      {lines.length > 0 && (
        <ul className="ping-lines">{lines.map((l) => <li key={l.seq}>{l.text}</li>)}</ul>
      )}
      {finished && <p role="status">{sent} sent, {received} received</p>}
    </div>
  )
}
