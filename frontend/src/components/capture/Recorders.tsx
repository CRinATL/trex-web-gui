import { useRef, useState } from 'react'
import { recorderRemove, recorderSave, recorderStop, type CaptureRow, type SpoolFile } from '../../api'
import { errText } from '../../portGate'
import { useAppState } from '../../state'
import { Info } from '../../ui/Info'
import { AddRecorderDialog } from './AddRecorderDialog'
import type { SpoolSource } from './useSpoolListing'
import { RunButton } from '../RunButton'

const origin = (r: CaptureRow) => (r.service ? 'TRex service' : r.mine ? 'this GUI' : 'other')
const ports = (p: number[]) => (p.length ? p.join(', ') : 'none')

// The hardware effect the help text states: a stop (never a remove: parse_cmd_remove, trex-core
// src/stx/common/trex_rpc_cmds_common.cpp:1582-1608, does not call it) ends in TrexStateless::unset_capture_feature() only when
// stateless mode has DP Rx queues (trex-core src/stx/common/trex_rpc_cmds_common.cpp:1502-1504). `--software` makes drivers
// with the drop-queue filter multi-queue too (src/main_dpdk.cpp:901-902, src/trex_modes.cpp:181-187). That function skips every
// port pair that is still the Rx of an ACTIVE capture (src/stx/stl/trex_stl.cpp:323-345), judged from the global filter that a stop
// rebuilds from the active captures only (src/stx/common/trex_capture.cpp:921-923, :822-826). Read in the TREX_SRC clone.
// Every capture the server lists. Stop, Save and Remove go to the server; the monitor's own row has none of them,
// because the loop that feeds the monitor owns that capture (the backend answers 409 capture_busy).
export function Recorders({ spool }: { spool: SpoolSource }) {
  const { capture, captureSave, session } = useAppState()
  const [adding, setAdding] = useState(false)
  const [confirming, setConfirming] = useState<number | null>(null)
  const [pending, setPending] = useState<number[]>([])      // ids with a command in flight
  const [errors, setErrors] = useState<Record<number, string>>({})
  // What each Save call itself returned: the file when it resolved, null when it rejected. A progress message can be the
  // last one the page never received, so the call's own result is what ends the row's Save (p3c-capture-120).
  const [results, setResults] = useState<Record<number, SpoolFile | null>>({})
  // TRex capture ids restart at 1 on every server: a result or error never outlives its connection. Adjusting state
  // during render, as StatusBar does with seenState.
  const [seenSeq, setSeenSeq] = useState(session.connect_seq)
  if (session.connect_seq !== seenSeq) { setSeenSeq(session.connect_seq); setResults({}); setErrors({}) }
  const inFlight = useRef(new Set<number>())
  const monitorId = capture.monitor?.id ?? null
  // SpoolFile.server is the `host:sync_port` the backend recorded (sessions/capture.py recorder_save).
  const server = session.params ? `${session.params.host}:${session.params.sync_port}` : null
  const ctl = session.state === 'connected' && !(session.params?.read_only ?? false)

  async function run(id: number, fn: () => Promise<unknown>) {
    if (inFlight.current.has(id)) return
    inFlight.current.add(id); setPending((p) => [...p, id]); setErrors((e) => ({ ...e, [id]: '' }))
    try { await fn() }
    catch (x) { setErrors((e) => ({ ...e, [id]: errText(x) })) }
    finally { inFlight.current.delete(id); setPending((p) => p.filter((x) => x !== id)) }
  }

  // A Save click forgets the last result of this capture, then records how the call ended.
  function saveRow(id: number) {
    setResults((m) => Object.fromEntries(Object.entries(m).filter(([k]) => Number(k) !== id)))
    void run(id, async () => {
      try { const res = await recorderSave(id); setResults((m) => ({ ...m, [id]: res.file })) }
      catch (x) { setResults((m) => ({ ...m, [id]: null })); throw x }
    })
  }

  function remove(r: CaptureRow, unsaved: boolean) {
    if (unsaved && confirming !== r.id) { setConfirming(r.id); return }
    setConfirming(null)
    void run(r.id, () => recorderRemove(r.id))
  }

  return (
    <section className="recorders" aria-label="Recorders">
      <h2>Recorders</h2>
      <div className="toolbar">
        <button type="button" disabled={!ctl} onClick={() => setAdding(true)}>Add recorder</button>
        <Info label="recorders">A recorder keeps packets on the server until you save them to a file. The server holds 10 captures in all, stopped ones included. On some NICs (TRex stateless with more than one DP core and a driver without the drop-queue filter, such as virtio, vmxnet3 or iavf) stopping a capture (Stop, or Save of a running recorder) makes the server turn the NIC's capture path off for port pairs that no running capture still receives on. Removing a capture does not. On af_packet, i40e, ice, ixgbe and mlx5 it does not happen, except when TRex runs with --software and more than one DP core. Read from the server source, not measured here.</Info>
      </div>
      {capture.captures.length === 0 ? <p className="muted">No captures on the server.</p> : (
        <table>
          <thead><tr><th>ID</th><th>Origin</th><th>State</th><th>Mode</th><th>Tx</th><th>Rx</th><th>BPF</th><th>Packets</th><th>Fetched</th><th>Bytes</th><th></th></tr></thead>
          <tbody>
            {capture.captures.map((r) => {
              const isMonitor = r.id === monitorId
              const msg = captureSave[r.id]
              const saving = pending.includes(r.id) || (msg !== undefined && msg.file === null && msg.error === null && !(r.id in results))
              const file = msg?.file ?? results[r.id] ?? null
              const saved = file !== null && !file.partial
              // A partial file of this capture on this server survives a reconnect and a reload, so it comes from the listing.
              const partial = (spool.listing?.files ?? []).some((f) => f.partial && f.capture_id === r.id && f.server === server)
              const resumable = partial || (msg?.error ?? null) !== null || Boolean(errors[r.id])
              const bufferRow = r.endpoint === null && !r.service && !isMonitor
              const error = errors[r.id] || msg?.error || ''
              return (
                <tr key={r.id}>
                  <td>{r.id}</td>
                  <td>{origin(r)}{isMonitor && ' (monitor)'}</td>
                  <td>{r.state}</td>
                  <td>{r.endpoint !== null ? `endpoint ${r.endpoint}` : r.mode ?? ''}</td>
                  <td>{ports(r.tx)}</td>
                  <td>{ports(r.rx)}</td>
                  <td>{r.bpf}</td>
                  <td>{r.count}/{r.limit}</td>
                  <td>{r.fetched}</td>
                  <td>{r.bytes}</td>
                  <td>
                    {bufferRow && r.state === 'ACTIVE' && (
                      <RunButton kind="stop" disabled={!ctl || saving} onClick={() => void run(r.id, () => recorderStop(r.id))}>Stop</RunButton>
                    )}
                    {bufferRow && !saved && (
                      <button type="button" disabled={!ctl || saving} onClick={() => saveRow(r.id)}>{resumable ? 'Resume' : 'Save'}</button>
                    )}
                    {!isMonitor && (
                      <button type="button" disabled={!ctl || r.service || saving} onBlur={() => setConfirming(null)}
                        onClick={() => remove(r, r.endpoint === null && r.count > 0 && !saved)}>
                        {confirming === r.id ? `${r.count} packets will be lost. Remove? Click again to confirm` : 'Remove'}
                      </button>
                    )}
                    {saving && msg && <span role="status"> fetched {msg.fetched} of {msg.total}</span>}
                    {saved && <span> Saved as {file.name}</span>}
                    {msg?.note && <span className="muted"> {msg.note}</span>}
                    {error && <span role="alert" className="error"> {error}</span>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
      {adding && <AddRecorderDialog onClose={() => setAdding(false)} />}
    </section>
  )
}
