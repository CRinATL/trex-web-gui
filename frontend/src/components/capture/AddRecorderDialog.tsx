import { useRef, useState } from 'react'
import { recorderStart } from '../../api'
import { captureBlock, LARGE_LIMIT, LARGE_LIMIT_NOTE, limitValid, MAX_LIMIT, serviceOffPorts, turnServiceOn } from '../../captureGate'
import { useHotkey } from '../../keys'
import { errText } from '../../portGate'
import { useAppState } from '../../state'
import { Info } from '../../ui/Info'
import { useDialogFocus } from '../useDialogFocus'
import { PortPicker } from './PortPicker'

const DEFAULT_LIMIT = 1000
const SLOTS_USED = 9           // the server holds 10; one is kept for ping, ARP and scan

// Starts a recorder: a fixed-size capture that stays on the server until saved. Gated like the monitor.
export function AddRecorderDialog({ onClose }: { onClose: () => void }) {
  const { capture, ports, session } = useAppState()
  const ref = useDialogFocus<HTMLDivElement>('[data-autofocus]')
  useHotkey('escape', onClose)
  const [tx, setTx] = useState<number[]>([])
  const [rx, setRx] = useState<number[]>([])
  const [bpf, setBpf] = useState('')
  const [limitText, setLimitText] = useState(String(DEFAULT_LIMIT))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inFlight = useRef(false)

  const limit = Number(limitText)
  const limitOk = limitValid(limitText)
  const connected = session.state === 'connected'
  const readOnly = session.params?.read_only ?? false
  const full = capture.captures.length >= SLOTS_USED
  const block = captureBlock({ connected, readOnly, monitorActive: false, rxPorts: rx, ports })
    ?? (full ? `${capture.captures.length} of 10 server slots used; one is kept for ping, ARP and scan`
      : tx.length === 0 && rx.length === 0 ? 'choose at least one port' : !limitOk ? `the limit is 1 to ${MAX_LIMIT}` : null)
  const off = serviceOffPorts(rx, ports)

  async function command(fn: () => Promise<unknown>, closeAfter: boolean) {
    if (inFlight.current) return
    inFlight.current = true; setBusy(true); setError(null)
    try { await fn(); if (closeAfter) onClose() }
    catch (x) { setError(errText(x)) }
    finally { inFlight.current = false; setBusy(false) }
  }
  const ok = () => command(() => recorderStart({ tx, rx, bpf, limit }), true)
  const enableService = () => command(async () => {
    await turnServiceOn(off)
  }, false)

  return (
    <div className="modal" role="dialog" aria-modal="true" aria-labelledby="add-recorder-title" ref={ref}>
      <h2 id="add-recorder-title">Add recorder</h2>
      <div className="form-grid">
        <PortPicker label="Tx ports" ports={ports} value={tx} onChange={setTx}
          note={<Info label="Tx capture">On the af_packet test guest a Tx capture recorded nothing unless that port was in service mode; other NICs are unverified.</Info>} />
        <PortPicker label="Rx ports" ports={ports} value={rx} onChange={setRx} />
        <div className="form-row"><label htmlFor="recorder-bpf">BPF filter</label><input id="recorder-bpf" type="text" value={bpf} data-autofocus onChange={(e) => setBpf(e.target.value)} /></div>
        <div className="form-row"><label htmlFor="recorder-limit">Limit</label><input id="recorder-limit" type="number" min={1} max={MAX_LIMIT} value={limitText} onChange={(e) => setLimitText(e.target.value)} /></div>
      </div>
      {limit > LARGE_LIMIT && <p className="muted">{LARGE_LIMIT_NOTE}</p>}
      {full && <p className="muted">{capture.captures.length} of 10 server slots used; one is kept for ping, ARP and scan</p>}
      {block && !full && <p className="muted">{block}</p>}
      {off.length > 0 && connected && !readOnly && (
        <button type="button" data-run="go" disabled={busy} onClick={() => void enableService()}>Turn service mode on for ports {off.join(', ')}</button>
      )}
      {error && <p role="alert" className="error">{error}</p>}
      <div className="dialog-footer">
        <div className="end">
          <button type="button" onClick={onClose}>Cancel</button>
          <button type="button" disabled={busy || block !== null} title={block ?? undefined} onClick={() => void ok()}>OK</button>
        </div>
      </div>
    </div>
  )
}
