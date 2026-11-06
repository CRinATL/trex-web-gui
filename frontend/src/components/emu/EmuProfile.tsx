import { useEffect, useRef, useState } from 'react'
import { ApiError, emuNamespaces, emuProfileInspect, emuProfileLoad, emuProfileRemove, type EmuNamespaces, type EmuProfileInspected } from '../../api'
import { emuStore, useEmu } from '../../emuStore'
import { defaultsFor, tunablesToArgs } from '../../traffic'
import { BrowseDialog } from '../BrowseDialog'
import { ConfirmDialog } from '../daemon/ConfirmDialog'
import { TunablesForm } from '../TunablesForm'

const failText = (x: unknown) => (x instanceof ApiError || x instanceof Error ? x.message : String(x))
// The EMU profile picker lists only Python files: the backend refuses any other suffix (422).
const PY = ['.py'] as const

type Pending = 'load' | 'remove'

// What is on the server now, read as the confirm opens: counts, and how many namespaces this app did not add. A listing that was interrupted
// (another client was changing the server) cannot rule foreign ones out: the text says so without inventing a count, and the tick is
// asked for up front rather than after the server's refusal.
function ServerNow({ onRead, ack, onAck, needAck }: { onRead: (foreign: number, interrupted: boolean) => void; ack: boolean; onAck: (v: boolean) => void; needAck: boolean }) {
  const [read, setRead] = useState<{ data: EmuNamespaces } | { error: string } | null>(null)
  useEffect(() => {
    let live = true
    emuNamespaces().then((data) => { if (live) { setRead({ data }); onRead(data.namespaces.filter((n) => !n.ours).length, data.interrupted) } },
      (x: unknown) => { if (live) setRead({ error: failText(x) }) })
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- read once, as the dialog opens
  }, [])
  const data = read && 'data' in read ? read.data : null
  const foreign = data ? data.namespaces.filter((n) => !n.ours) : []
  const clients = data ? data.namespaces.reduce((a, n) => a + n.active_clients, 0) : 0
  const clientsForeign = foreign.reduce((a, n) => a + n.active_clients, 0)
  return (
    <>
      {!read && <p className="muted">Reading what the server holds…</p>}
      {read && 'error' in read && <p className="muted">Could not read what the server holds: {read.error}</p>}
      {data && <p>On the server now: {data.namespaces.length} namespace(s), {clients} client(s)
        {foreign.length > 0 ? `, of which ${foreign.length} namespace(s) with ${clientsForeign} client(s) were not added by this app` : ''}.
        {data.interrupted ? ' Another client was changing the server while it was read, so this may be incomplete.' : ''}</p>}
      {(foreign.length > 0 || needAck) && (
        <label className="check">
          <input type="checkbox" checked={ack} onChange={(e) => onAck(e.target.checked)} />{' '}
          {foreign.length > 0
            ? 'Remove the namespaces this app did not add as well'
            : 'The server holds namespaces this app did not add, or another client was changing it during the check. Remove them anyway'}
        </label>
      )}
    </>
  )
}

// The profile block of the EMU page: pick a `.py` EMU profile, Inspect it (counts, plugins, vports, warnings), Load it (replacing everything on the
// server) or Remove everything. Both writes go through a confirm that names the target and what the server holds now; namespaces this app did not
// add need a second tick. Progress follows the `emu` message; a failed write is shown with how far it got.
export function EmuProfile() {
  const state = useEmu((m) => m?.state ?? 'disconnected')
  const target = useEmu((m) => m?.target ?? null)
  const busy = useEmu((m) => m?.busy ?? null)
  const loaded = useEmu((m) => m?.loaded ?? null)
  const [path, setPath] = useState('')
  const [browsing, setBrowsing] = useState(false)
  const [inspected, setInspected] = useState<EmuProfileInspected | null>(null)
  const [values, setValues] = useState<Record<string, unknown>>({})
  const [extra, setExtra] = useState('')
  const [rate, setRate] = useState('')
  const [inspecting, setInspecting] = useState(false)
  const [inspectErr, setInspectErr] = useState<string | null>(null)
  const [pending, setPending] = useState<Pending | null>(null)
  const [ack, setAck] = useState(false)
  const [needAck, setNeedAck] = useState(false)
  const [foreignCount, setForeignCount] = useState(0)
  const [writeErr, setWriteErr] = useState<string | null>(null)
  const [needCrash, setNeedCrash] = useState(false)
  const [crashAck, setCrashAck] = useState(false)
  const [failedOp, setFailedOp] = useState<Pending | null>(null)   // the write that last failed in this page
  const inspectTok = useRef(0)    // bumped when the path changes: an Inspect reply of an older path is dropped

  const rateNo = Number(rate)
  const rateOk = rate.trim() === '' || (Number.isFinite(rateNo) && rateNo >= 1 && rateNo <= 100000)
  const connected = state === 'connected'
  const trimmed = path.trim()
  const args = () => (inspected ? tunablesToArgs(inspected.tunables, values, extra) : [])
  const targetText = target ? `${target.host}:${target.port}` : 'the EMU server'

  function editPath(p: string) {
    inspectTok.current++
    setWriteErr(null)                      // a write error belongs to the profile it was about
    setPath(p); setInspected(null); setInspectErr(null); setValues({}); setExtra(''); setInspecting(false)
  }
  async function inspect() {
    const tok = inspectTok.current
    setInspecting(true); setInspectErr(null)
    try {
      const r = await emuProfileInspect(trimmed, args())
      if (tok !== inspectTok.current) return     // the path changed meanwhile: this reply is for another profile
      // same profile: keep the user's values; a first inspect starts from the defaults
      setValues((v) => (inspected ? v : defaultsFor(r.tunables)))
      setInspected(r)
    } catch (x) { if (tok === inspectTok.current) setInspectErr(failText(x)) } finally { if (tok === inspectTok.current) setInspecting(false) }
  }
  function open(kind: Pending) { setAck(false); setNeedAck(false); setNeedCrash(false); setCrashAck(false); setForeignCount(0); setWriteErr(null); setPending(kind) }
  // Sends the write; confirm and foreign_ack go as the booleans, crash_ack only when ticked. With foreign namespaces shown and no tick nothing is sent.
  async function write(kind: Pending) {
    if ((foreignCount > 0 || needAck) && !ack) throw new Error('Tick the box to remove the namespaces this app did not add, or cancel.')
    const o = { confirm: true, foreign_ack: ack, ...(crashAck ? { crash_ack: true } : {}), ...(rate.trim() === '' ? {} : { max_rate: rateNo }) }
    setWriteErr(null)
    try {
      const m = kind === 'load' ? await emuProfileLoad(trimmed, args(), o) : await emuProfileRemove(o)
      emuStore.push(m)
      setPending(null)
      setFailedOp(null)
    } catch (x) {
      if (x instanceof ApiError && x.code === 'foreign_namespaces') setNeedAck(true)
      if (x instanceof ApiError && x.code === 'crash_risk') setNeedCrash(true)
      // A refusal (409) or a rejected body (422) sent no write, so it says nothing about a partial profile.
      if (!(x instanceof ApiError && (x.status === 409 || x.status === 422))) setFailedOp(kind)
      setWriteErr(failText(x))
      throw x
    }
  }
  const sub = (
    <>
      <ServerNow onRead={(n, interrupted) => { setForeignCount(n); if (interrupted) setNeedAck(true) }} ack={ack} onAck={setAck} needAck={needAck} />
      {needCrash && (
        <label className="check">
          <input type="checkbox" checked={crashAck} onChange={(e) => setCrashAck(e.target.checked)} />{' '}
          Remove anyway: the EMU server will crash and must be restarted
        </label>
      )}
    </>
  )

  return (
    <section className="emu-profile">
      <h2>Profile</h2>
      <div className="form-row">
        <label>EMU profile (.py) <input value={path} onChange={(e) => editPath(e.target.value)} spellCheck={false} /></label>
        <button type="button" onClick={() => setBrowsing(true)}>Browse…</button>
        <button type="button" disabled={!trimmed || inspecting} onClick={() => void inspect()}>Inspect</button>
        <label>Rate limit (clients/s) <input className="narrow" type="number" min={1} max={100000} value={rate} onChange={(e) => setRate(e.target.value)} /></label>
      </div>
      {browsing && <BrowseDialog title="Pick an EMU profile (.py)" suffixes={PY} onClose={() => setBrowsing(false)} onPick={(p) => { setBrowsing(false); editPath(p) }} />}
      {inspectErr && <p role="alert" className="error">{inspectErr}</p>}
      {inspected && (
        <>
          <TunablesForm specs={inspected.tunables} values={values} extra={extra} onValues={setValues} onExtra={setExtra} />
          <p>{inspected.counts.namespaces} namespace(s), {inspected.counts.clients} client(s); plugins: {inspected.plugins.length ? inspected.plugins.join(', ') : 'none'};
            vports: {inspected.vports.length ? inspected.vports.join(', ') : 'none'}</p>
          {inspected.warnings.map((w) => <p key={w} className="muted" role="status">{w}</p>)}
        </>
      )}
      {!rateOk && <p role="alert" className="error">The rate limit must be 1 to 100000 clients per second, or empty.</p>}
      <div className="dialog-footer">
        <button type="button" disabled={!connected || !!busy || !trimmed || !rateOk} onClick={() => open('load')}>Load…</button>
        <button type="button" disabled={!connected || !!busy || !rateOk} onClick={() => open('remove')}>Remove all…</button>
      </div>
      {loaded && <p className="muted">Loaded from this app: {loaded.path}, {loaded.namespaces} namespace(s), {loaded.clients} client(s).</p>}
      {loaded?.partial && <p role="alert" className="error">A partial profile is on the server: {failedOp === 'load' ? 'the last load' : failedOp === 'remove' ? 'the last removal' : 'a load or removal'} did not finish.</p>}
      {writeErr && !pending && <p role="alert" className="error">{writeErr}</p>}
      {pending === 'load' && (
        <ConfirmDialog title="Load the profile" confirmLabel="Load" danger onClose={() => setPending(null)} onConfirm={() => write('load')}
          body={<><p>Load {trimmed} onto {targetText}. Everything on the server is removed first. A loaded profile can start sending traffic on its ports at once (the DHCP server plugin, for one, can starve a network).</p>{sub}</>} />
      )}
      {pending === 'remove' && (
        <ConfirmDialog title="Remove everything" confirmLabel="Remove" danger onClose={() => setPending(null)} onConfirm={() => write('remove')}
          body={<><p>Remove every namespace and client from {targetText}.</p>{sub}</>} />
      )}
    </section>
  )
}
