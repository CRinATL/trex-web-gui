import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { downloadBlob } from '../../download'
import { monitorStart, monitorStop, wiresharkPreflight, wiresharkStart, wiresharkStop, type DecodedRow } from '../../api'
import { captureStore } from '../../captureStore'
import { captureBlock, LARGE_LIMIT, LARGE_LIMIT_NOTE, limitValid, MAX_LIMIT, serviceOffPorts, turnServiceOn } from '../../captureGate'
import { toPcap } from '../../pcapWriter'
import { errText } from '../../portGate'
import { useAppState } from '../../state'
import { PacketTable } from './PacketTable'
import { Info } from '../../ui/Info'
import { PortPicker } from './PortPicker'
import { RunButton } from '../RunButton'

const DEFAULT_LIMIT = 100
const stamp = () => new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z')

// The live monitor: its ports, filter and limit, the counters, and the packets the browser holds.
// Changing the filter on a running monitor is Stop, then Start.
export function CaptureMonitor({ onOpen, active: shown = true }: { onOpen: (row: DecodedRow) => void; active?: boolean }) {
  const { capture, ports, session } = useAppState()
  const monitor = capture.monitor
  const rows = useSyncExternalStore(captureStore.subscribe, captureStore.rows)
  const browserDropped = useSyncExternalStore(captureStore.subscribe, captureStore.droppedByBrowser)
  const [tx, setTx] = useState<number[]>([])
  const [rx, setRx] = useState<number[]>([])
  const [bpf, setBpf] = useState('')
  const [limitText, setLimitText] = useState(String(DEFAULT_LIMIT))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Clear empties the browser's rows on purpose: the rows and drops it removed are counted per monitor, so they are not "not shown".
  // The key carries connect_seq as well: a reconnect can hand out the same monitor id again.
  const [cleared, setCleared] = useState<{ key: string; n: number }>({ key: '', n: 0 })
  const inFlight = useRef(false)
  // Asked each time the page is shown: the path is the saved preference, changed on the Settings page (another page, so
  // coming back re-asks), and the backend may have been offline the last time.
  const [preflight, setPreflight] = useState<{ ok: boolean; reason: string | null } | null>(null)
  useEffect(() => {
    if (!shown) return
    let live = true
    wiresharkPreflight().then((r) => { if (live) setPreflight(r) }).catch((x) => { if (live) setPreflight({ ok: false, reason: errText(x) }) })
    return () => { live = false }
  }, [shown])

  const limit = Number(limitText)
  const limitOk = limitValid(limitText)
  const monitorKey = `${session.connect_seq ?? ''}/${monitor?.id ?? ''}`
  const active = monitor !== null && !monitor.gone
  const connected = session.state === 'connected'
  const readOnly = session.params?.read_only ?? false
  const block = captureBlock({ connected, readOnly, monitorActive: active, rxPorts: rx, ports })
    ?? (tx.length === 0 && rx.length === 0 ? 'choose at least one port' : !limitOk ? `the limit is 1 to ${MAX_LIMIT}` : null)
  const off = serviceOffPorts(rx, ports)

  async function command(fn: () => Promise<unknown>) {
    if (inFlight.current) return
    inFlight.current = true; setBusy(true); setError(null)
    try { await fn() }
    catch (x) { setError(errText(x)) }
    finally { inFlight.current = false; setBusy(false) }
  }

  const start = () => command(async () => {
    await monitorStart({ tx, rx, bpf, limit })
    // Only an accepted Start empties the rows (a refused one keeps them). The first fetch comes after this returns.
    captureStore.clear()
    setCleared({ key: '', n: 0 })
  })
  const enableService = () => command(async () => {
    await turnServiceOn(off)
  })

  function clear() {
    if (monitor) setCleared({ key: monitorKey, n: (cleared.key === monitorKey ? cleared.n : 0) + rows.length + browserDropped })
    captureStore.clear()
  }

  function download() {
    downloadBlob(`monitor-${stamp()}.pcap`, toPcap(rows))
  }

  // fetched lags the rows by up to one poll, so both differences can be negative for a moment: clamp them at 0.
  const base = monitor && cleared.key === monitorKey ? cleared.n : 0
  const notShown = monitor ? Math.max(0, monitor.fetched - base - rows.length - browserDropped) : 0
  const serverDropped = monitor ? Math.max(0, monitor.matched - monitor.fetched - monitor.count) : 0
  const ctl = connected && !readOnly
  const ws = capture.wireshark
  const wsBlock = preflight === null ? 'checking whether Wireshark can be launched'
    : !preflight.ok ? preflight.reason ?? 'Wireshark cannot be launched'
    : !connected ? 'not connected'
    : !active ? 'start the monitor first' : null

  return (
    <section className="capture-monitor" aria-label="Monitor">
      <div className="form-grid">
        <PortPicker label="Tx ports" ports={ports} value={tx} onChange={setTx} disabled={active}
          note={<Info label="the af_packet note">On the af_packet test guest a Tx capture recorded nothing unless that port was in service mode; other NICs are unverified.</Info>} />
        <PortPicker label="Rx ports" ports={ports} value={rx} onChange={setRx} disabled={active} />
        <label htmlFor="capture-bpf">BPF filter</label>
        <input id="capture-bpf" type="text" value={bpf} disabled={active} onChange={(e) => setBpf(e.target.value)} />
        <label htmlFor="capture-limit">Limit</label>
        <span>
          <input id="capture-limit" type="number" min={1} max={MAX_LIMIT} value={limitText} disabled={active} onChange={(e) => setLimitText(e.target.value)} />
          {limit > LARGE_LIMIT && <Info label="the limit">{LARGE_LIMIT_NOTE}</Info>}
        </span>
      </div>
      <div className="action-bar">
        <RunButton kind="go" lit={active} litLabel="Running" disabled={busy || block !== null} title={block ?? undefined} onClick={() => void start()}>Start</RunButton>
        <RunButton kind="stop" disabled={busy || !ctl || !active} onClick={() => void command(monitorStop)}>Stop</RunButton>
        <button type="button" onClick={clear}>Clear</button>
        <button type="button" disabled={rows.length === 0} onClick={download}>Download pcap</button>
        <RunButton kind="go" lit={ws.running} litLabel="Wireshark open" disabled={busy || wsBlock !== null} title={ws.running ? undefined : wsBlock ?? undefined}
          onClick={() => void command(wiresharkStart)}>Open in Wireshark</RunButton>
        <RunButton kind="stop" disabled={busy || !ws.running} onClick={() => void command(wiresharkStop)}>Stop Wireshark</RunButton>
      </div>
      {block && <p className="muted">{block}</p>}
      {off.length > 0 && ctl && !active && (
        <button type="button" data-run="go" disabled={busy} onClick={() => void enableService()}>Turn service mode on for ports {off.join(', ')}</button>
      )}
      {preflight && !preflight.ok && <p className="muted">Wireshark: {preflight.reason}</p>}
      {ws.error && <p role="status" className="error">Wireshark: {ws.error}</p>}
      {error && <p role="alert" className="error">{error}</p>}
      {monitor?.gone && <p role="status" className="error">Capture {monitor.id} was removed on the server ({monitor.gone}).</p>}
      {monitor && (
        <p role="group" aria-label="Counters" className="counters-line">
          <span className="k">Fetched</span> <span>{monitor.fetched}</span>
          <span className="k">Matched</span> <span>{monitor.matched}</span>
          <span className="k">Server dropped</span> <span>{serverDropped}</span>
          <span className="k">Browser dropped</span> <span>{browserDropped}</span>
        </p>
      )}
      {notShown > 0 && <p className="muted">{notShown} {notShown === 1 ? 'packet' : 'packets'} not shown</p>}
      <div className="data-table-scroll">
        <PacketTable rows={rows} onOpen={onOpen}
          empty={!active ? 'No packets. Start the monitor to capture.' : 'No packets yet: waiting for the first one that matches.'} />
      </div>
    </section>
  )
}
