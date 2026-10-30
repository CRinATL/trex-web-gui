import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { ApiError, assign, assignedOn, createProfile, importProfile, rate, update, type AssignedRecord, type LibraryEntry, type PortView, type StreamSummary } from '../api'
import { actionsFor, defaultState, isRunning, TRANSMITTING, type MenuAction, type Selection } from '../portActions'
import { defaultsFor, parseMult, tunablesToArgs } from '../traffic'
import { formatRate, fromWireMult, parseRate, readingOf, toWireMult } from '../rateText'
import { useOptionalNavigate } from '../routerSafe'
import { trafficStore, type TrafficForm } from '../trafficStore'
import { sentence } from '../portGate'
import { canUpdateRate, startFromForm, startSummary } from '../trafficStart'
import { ProfilePicker } from './ProfilePicker'
import { rateLine } from './rate'
import { fillFromPy, PY_NOTE, pySourceOf } from './studio/libraryPick'
import { TunablesForm } from './TunablesForm'
import { RunButton } from './RunButton'
import { EditedTag } from '../ui/Edited'

const errText = (x: unknown) => (x instanceof Error ? x.message : String(x))
const stemOf = (p: string) => (p.split('/').pop() ?? p).replace(/\.[^.]*$/, '')
// The box text for a stored multiplier.
const multText = (value: string, unit: string): string => {
  const r = fromWireMult(`${value}${unit === 'x' ? '' : unit}`)
  return r ? formatRate(r) : value
}
const COUNTDOWN_S = 10
const CONFIRM_MS = 5000
export type FocusTarget = 'profile' | 'mult'

// A profile's streams, as inspect and Assign summarise them.
function StreamsTable({ summary, count, label }: { summary: StreamSummary[]; count: number; label: string }) {
  return (
    <>
      <table className="streams" aria-label={label}>
        <thead><tr><th>#</th><th>Name</th><th>Enabled</th><th>Mode</th><th>Rate</th><th>Packet</th><th>Length</th><th>VM</th></tr></thead>
        <tbody>
          {summary.map((s) => (
            <tr key={s.index}><td>{s.index}</td><td>{s.name ?? ''}</td><td>{s.enabled ? 'yes' : 'no'}</td><td>{s.mode}</td><td>{s.rate}</td>
              <td>{s.packet}</td><td>{s.length}</td><td>{s.has_vm ? 'yes' : 'no'}</td></tr>
          ))}
        </tbody>
      </table>
      {count > summary.length && <p className="muted">Showing the first {summary.length} of {count} streams</p>}
    </>
  )
}
const sameArgs = (a: string[], b: string[]) => a.length === b.length && a.every((x, i) => x === b[i])

export function TrafficTab({ port, selection, ports, readOnly, connected, visible = true, focusTarget, requestError, onFocused, onResolved }: {
  port: PortView; selection: Selection; ports: PortView[]; readOnly: boolean; connected: boolean
  visible?: boolean   // false while another tab is shown: a running countdown is cancelled
  focusTarget?: FocusTarget | null; onFocused?: () => void
  onResolved?: () => void   // a command resolved: the page clears its alert line
  requestError?: { text: string; seq: number } | null   // a failure from a request made elsewhere (the toolbar's Start), keyed by the request; shown until the next edit or command
}) {
  const id = port.id
  // The per-port form lives in trafficStore: it survives a port switch (this tab is keyed by port) and saved setups read it.
  const form = useSyncExternalStore(trafficStore.subscribe, () => trafficStore.get(id))
  const { path, inspected, values, extra, specs, formPath, multValue, unit, applied, duration, force, pending, libraryPy, libraryArgs } = form
  const patch = (p: Partial<TrafficForm>) => trafficStore.set(id, p)
  // The store's reset count when this render ran: a reply that lands after a connect_seq reset must write nothing.
  const gen = trafficStore.generation()
  const patchIf = (g: number, p: Partial<TrafficForm>) => { if (trafficStore.generation() === g) patch(p) }
  // Every edit clears the alert raised by a request made elsewhere.
  const setValues = (v: Record<string, unknown>) => { setReqErr(null); patch({ values: v }) }
  const setExtra = (v: string) => { setReqErr(null); patch({ extra: v }) }
  const setDuration = (v: string) => { setReqErr(null); patch({ duration: v }) }
  const setForce = (v: boolean) => { setReqErr(null); patch({ force: v }) }
  // What is on the port (RC1-30): Start sends that, not the profile in the path box. null until read, or when not readable.
  const [onPort, setOnPort] = useState<{ streams: boolean; assigned: AssignedRecord | null } | null>(null)
  const [onPortSeq, setOnPortSeq] = useState(0)   // bumped after an Assign: read the port again
  const [rateInfo, setRateInfo] = useState<Record<string, unknown> | null>(null)
  const setPending = (p: TrafficForm['pending']) => trafficStore.set(id, { pending: p })
  const [confirmAssign, setConfirmAssign] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [reqErr, setReqErr] = useState<string | null>(null)
  const navigate = useOptionalNavigate()
  const [naming, setNaming] = useState<string | null>(null)   // the inline name of New in Studio…, null while closed
  const [studioBusy, setStudioBusy] = useState(false)
  const [studioErr, setStudioErr] = useState<string | null>(null)
  const [seenReqSeq, setSeenReqSeq] = useState(requestError?.seq ?? 0)   // a remount must not replay an old request's error
  if (requestError && requestError.seq !== seenReqSeq) { setSeenReqSeq(requestError.seq); setReqErr(requestError.text) }   // adjusting state during render, as PortPanel does
  const inFlight = useRef(false)
  const confirmTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const pathRef = useRef<HTMLInputElement>(null)
  const multRef = useRef<HTMLInputElement>(null)

  const acts = actionsFor(selection, ports, readOnly, connected)
  const act = (aid: string): MenuAction | undefined => acts.find((a) => a.id === aid)
  // Start lights from the default profile `_`, as the toolbar's does: a foreign profile transmitting leaves `_`'s Start usable
  const dflt = defaultState(port)
  const needsStop = isRunning(port)

  useEffect(() => {
    if (!focusTarget) return
    ;(focusTarget === 'profile' ? pathRef : multRef).current?.focus()
    onFocused?.()
  }, [focusTarget, onFocused])
  useEffect(() => () => clearTimeout(confirmTimer.current), [])
  // Read again whenever what the port holds may have changed: ownership, the default profile's state, an Assign here.
  const profileState = port.profiles['_'] ?? 'none'
  useEffect(() => {
    if (!connected || !visible) return
    let live = true
    assignedOn(id).then((r) => { if (live) setOnPort(r) }, () => { if (live) setOnPort(null) })
    return () => { live = false }
  }, [id, connected, visible, port.owned, profileState, onPortSeq])

  // Who may change the rate: the same rule that enables Update in the menu (connected, writable, owned, default profile).
  const canUpdate = canUpdateRate(selection, ports, readOnly, connected)
  const editBlock = !connected ? 'not connected' : readOnly ? 'read-only session' : !port.owned ? 'acquire the port first'
    : selection.kind === 'profile' && selection.profile !== '_' ? 'select the default profile' : undefined
  const locked = editBlock !== undefined

  // Live update (A3.3): the countdown only runs while the default profile transmits and this session may act.
  // Both panels stay mounted, so a hidden tab cancels it (a rate change the user can no longer see must not fire).
  // Both drops write the store, so they run in effects, never during render.
  const live = pending && canUpdate && visible ? pending : null
  useEffect(() => { if (pending && !canUpdate) setPending(null) })   // the port stopped or paused: an old update must never fire after Resume
  useEffect(() => { if (pending && !visible) cancelUpdate() })
  useEffect(() => {
    if (!live) return
    const t = setTimeout(() => {
      if (live.left > 1) setPending({ ...live, left: live.left - 1 })
      else if (inFlight.current) setPending({ ...live, left: 1 })   // another command is running: retry in a second, never drop it
      else void applyUpdate(live.mult)
    }, 1000)
    return () => clearTimeout(t)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live])

  // Runs one API command with the single in-flight guard; the result goes to the alert line.
  async function command(fn: () => Promise<unknown>): Promise<boolean> {
    if (inFlight.current) return false
    inFlight.current = true; setBusy(true); setError(null); setReqErr(null)
    try { await fn(); onResolved?.(); return true }
    catch (x) { setError(errText(x)); return false }
    finally { inFlight.current = false; setBusy(false) }
  }

  async function applyUpdate(m: string) {
    setPending(null)
    const g = trafficStore.generation()
    if (await command(() => update({ ports: [id], mult: m }))) { const sent = parseMult(m); if (sent) patchIf(g, { applied: { value: sent.value, unit: sent.unit } }) }
  }
  // The Multiplier box: its text is local, the store keeps the wire-shaped (multValue, unit) pair. Typing writes the store (so Start
  // sends what is typed) and never starts the countdown; Enter, a blur to another element, or the slider commits.
  const [text, setText] = useState(() => multText(multValue, unit))
  const [seen, setSeen] = useState({ multValue, unit })
  const [touched, setTouched] = useState(false)   // edited since the last commit or reload: a blur that changed nothing commits nothing
  if (seen.multValue !== multValue || seen.unit !== unit) {   // changed from outside (setup load, Cancel, another tab): show it
    setSeen({ multValue, unit }); setText(multText(multValue, unit)); setTouched(false)
  }
  const parsed = parseRate(text, 'factor')
  const wire = 'error' in parsed ? null : toWireMult(parsed)
  // shown live while typing; a text that reads but has no wire form (too small) gets the same copy
  const readErr = 'error' in parsed ? parsed.error : wire ? null : `Can't read "${text}": a number, optionally with k/M/G and pps, bps or %, or × for a factor.`
  function typeMult(t: string) {
    setReqErr(null); setText(t); setTouched(true)
    const r = parseRate(t, 'factor')
    const w = 'error' in r ? null : toWireMult(r)
    const m = w ? parseMult(w) : null
    const next = m ? { multValue: m.value, unit: m.unit } : { multValue: '', unit }   // '' makes formMult refuse it
    setSeen(next); patch(next)
  }
  function commitMult(t: string) {
    setTouched(false)
    const r = parseRate(t, 'factor')
    const w = 'error' in r ? null : toWireMult(r)
    if (!w) return   // unreadable: the error is already showing, nothing starts
    if (canUpdate) setPending({ mult: w, left: COUNTDOWN_S })
  }
  // Marked edited (RC1-31) when a commit would send another multiplier than the last one the server accepted, or the text is
  // unreadable and differs from the applied one's (R6): '1' over ×1 or '10kpps' over 10 kpps is not an edit.
  const sendsRate = wire ? fromWireMult(wire) : null
  const appliedRate = fromWireMult(`${applied.value}${applied.unit === 'x' ? '' : applied.unit}`)
  const multEdited = sendsRate && appliedRate ? sendsRate.kind !== appliedRate.kind || sendsRate.value !== appliedRate.value
    : text !== multText(applied.value, applied.unit)
  const multErrId = `traffic-mult-${id}-err`, multEdId = `traffic-mult-${id}-ed`
  function cancelUpdate() { setPending(null); patch({ multValue: applied.value, unit: applied.unit }) }

  // Start is shared with the toolbar; a FormError (bad multiplier or duration) lands in the alert line like any failure.
  const doStart = () => command(() => startFromForm(id))

  async function doAssign() {
    if (!inspected) return
    if (needsStop && !confirmAssign) {
      setConfirmAssign(true)
      clearTimeout(confirmTimer.current)
      confirmTimer.current = setTimeout(() => setConfirmAssign(false), CONFIRM_MS)
      return
    }
    clearTimeout(confirmTimer.current); setConfirmAssign(false)
    await command(async () => {
      const r = await assign({ ports: [id], path: inspected.path, tunables: tunablesToArgs(inspected.tunables, values, extra), ...(needsStop ? { stop_first: true } : {}) })
      setRateInfo(r.rate[String(id)] ?? null)
      setOnPortSeq((n) => n + 1)
    })
  }

  // A3.6: New in Studio… and Copy to Studio create a library entry and open it in Studio.
  async function toStudio(make: () => Promise<LibraryEntry | { rev: string }>) {
    setStudioBusy(true); setStudioErr(null)
    try {
      const r = await make()
      setNaming(null)
      if ('slug' in r) navigate?.('/studio', { state: { studioSelect: r.slug } })
    } catch (x) { setStudioErr(x instanceof ApiError || x instanceof Error ? x.message : String(x)) }
    finally { setStudioBusy(false) }
  }
  const copyToStudio = () => inspected && toStudio(() => importProfile({
    path: inspected.path, name: stemOf(inspected.path),
    ...(inspected.kind === 'py' ? { tunables: tunablesToArgs(inspected.tunables, values, extra), tunable_form: { values, extra } } : {}),
  }))

  const held = onPort?.assigned ?? null
  // The profile in the box is not what Start would send: another file, other arguments, or nothing assigned yet.
  const notOnPort = !!inspected && (held === null || held.path !== inspected.path
    || !sameArgs(held.tunables, tunablesToArgs(inspected.tunables, values, extra)))
  const assignEnabled = !!act('assign')?.enabled && !!inspected && !busy
  const tx = (aid: string) => !!act(aid)?.enabled && !busy

  return (
    <div className="traffic-tab">
      <section>
        <h3>Profile</h3>
        <ProfilePicker portId={id} path={path} inputRef={pathRef} disabled={readOnly} visible={visible} onLibraryPick={(e) => { const src = pySourceOf(e); if (!src) return; setReqErr(null); setError(null); fillFromPy(id, e, src) }}
          tunablesFor={(p) => (p !== formPath ? [] : specs.length === 0 && p === libraryPy ? libraryArgs : tunablesToArgs(specs, values, extra))}
          onPath={(p) => { setReqErr(null); patch({ path: p, inspected: null }) }}
          onInspected={(i) => {
            if (!i) return   // a failed inspect (a typo in the profile) keeps the form, its values and the last table
            if (trafficStore.generation() !== gen) return   // a connect_seq reset came first: write nothing
            const cur = trafficStore.get(id)   // read now: the inspect is async
            if (i.path === cur.formPath) {
              // same profile: keep the user's settings, dropping only dests the new specs no longer have
              const fresh = defaultsFor(i.tunables)
              const kept = Object.fromEntries(Object.entries(fresh).map(([k, d]) => [k, k in cur.values ? cur.values[k] : d]))
              patch({ inspected: i, specs: i.tunables, values: kept })
            } else patch({ inspected: i, specs: i.tunables, values: defaultsFor(i.tunables), extra: '', formPath: i.path })
          }} />
        {inspected && <TunablesForm specs={inspected.tunables} values={values} extra={extra} onValues={setValues} onExtra={setExtra} />}
        {inspected && notOnPort && (
          <>
            <p className="muted">In {stemOf(inspected.path)}, not on the port: Assign puts these streams on the port.</p>
            <StreamsTable summary={inspected.summary} count={inspected.stream_count} label="Streams in the profile" />
          </>
        )}
        <div className="action-bar">
          <button type="button" disabled={studioBusy || naming !== null} onClick={() => { setStudioErr(null); setNaming('') }}>New in Studio…</button>
          <button type="button" disabled={studioBusy || !inspected} onClick={() => void copyToStudio()}>Copy to Studio</button>
        </div>
        {naming !== null && (
          <div role="group" aria-label="New profile in Studio">
            <label>Name <input autoFocus value={naming} onChange={(e) => setNaming(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && naming.trim() && !studioBusy) void toStudio(() => createProfile(naming.trim())); if (e.key === 'Escape') setNaming(null) }} /></label>{' '}
            <button type="button" onClick={() => setNaming(null)}>Cancel</button>{' '}
            <button type="button" disabled={studioBusy || !naming.trim()} onClick={() => void toStudio(() => createProfile(naming.trim()))}>Create</button>
          </div>
        )}
        {studioErr && <p role="alert" className="error">{studioErr}</p>}
      </section>

      <section>
        <h3>On the port</h3>
        {held ? (
          <>
            <p>{held.name}{held.tunables.length > 0 && <span className="muted"> with {held.tunables.join(' ')}</span>}, assigned in this session. Start sends these streams.</p>
            <StreamsTable summary={held.summary} count={held.stream_count} label="Streams on the port" />
          </>
        ) : onPort?.streams ? <p className="muted">Streams this session did not assign (another session, or before a reconnect). Start sends them; Assign replaces them.</p>
          : onPort ? <p className="muted">No streams. Assign a profile to put some on the port.</p> : null}
        {!rateInfo && (
          <p><button type="button" disabled={busy || !act('rate')?.enabled} onClick={() => void command(async () => setRateInfo((await rate(id)).rate))}>Show rate at ×1</button></p>
        )}
        {rateInfo && (
          <div>
            <p>At ×1: {rateLine(rateInfo) ?? 'no rate figures in the response'}</p>
            <details><summary>Full validate response</summary><pre>{JSON.stringify(rateInfo, null, 2)}</pre></details>
          </div>
        )}
      </section>

      <section>
        <h3>Rate</h3>
        <div className="form-grid">
          <div className="form-row">
            <label htmlFor={`traffic-mult-${id}`}>Multiplier</label>
            <span>
              <input id={`traffic-mult-${id}`} ref={multRef} type="text" value={text} disabled={locked} title={editBlock}
                aria-invalid={readErr ? true : undefined} className={multEdited ? 'edited' : undefined}
                aria-describedby={[readErr && multErrId, multEdited && multEdId].filter(Boolean).join(' ') || undefined}
                onChange={(e) => typeMult(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') commitMult(e.currentTarget.value) }}
                onBlur={(e) => { if (touched && document.hasFocus()) commitMult(e.currentTarget.value) }} />
              {multEdited && <EditedTag id={multEdId} />}
              {!('error' in parsed) && parsed.kind === 'percentage' && <>{' '}<input type="range" min={0} max={100} aria-label="Multiplier percent" disabled={locked} title={editBlock} value={parsed.value}
                onChange={(e) => { typeMult(`${e.target.value}%`); commitMult(`${e.target.value}%`) }} /></>}
              {readErr ? <span id={multErrId} className="error"> {readErr}</span> : !('error' in parsed) && <span className="muted"> {readingOf(parsed)}</span>}
            </span>
          </div>
          <div className="form-row">
            <label htmlFor={`traffic-duration-${id}`}>Duration (s)</label>
            <input id={`traffic-duration-${id}`} className="narrow" type="number" min="0" step="any" placeholder="no limit" value={duration} onChange={(e) => setDuration(e.target.value)} />
          </div>
          <div className="form-row">
            <label htmlFor={`traffic-force-${id}`}>Force</label>
            <input id={`traffic-force-${id}`} type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
          </div>
        </div>
      </section>

      <div className="action-bar">
        <button type="button" disabled={!assignEnabled} className={needsStop ? 'danger' : undefined} onClick={() => void doAssign()}>
          {confirmAssign ? 'Click again to stop and assign' : needsStop ? 'Stop and assign' : 'Assign'}
        </button>
        <RunButton kind={dflt === 'PAUSE' ? 'hold' : 'go'} lit={dflt === 'PAUSE' || TRANSMITTING.includes(dflt)}
          litLabel={dflt === 'PAUSE' ? 'Paused' : 'Transmitting'} fits={['Transmitting', 'Paused']} disabled={!tx('start')}
          onClick={() => void doStart()}>Start</RunButton>
        {/* the reason the bar is blocked, shown as text (not only as a tooltip), else what Start will send */}
        {editBlock ? <span className="muted">{sentence(editBlock)}</span>
          : !inspected ? <span className="muted">Inspect a profile first.</span>
            : <span className="muted">{startSummary(form)}{notOnPort && held ? `, sending ${stemOf(held.path)}, not ${stemOf(inspected.path)}: assign it first` : ''}.</span>}
        {libraryPy && libraryPy === path && <span className="muted">{PY_NOTE}</span>}
        {live && (
          <span role="status" className="live-update">
            Applying {live.mult} in {live.left} s ·{' '}
            <button type="button" disabled={busy} onClick={() => void applyUpdate(live.mult)}>Apply now</button>{' '}
            <button type="button" onClick={cancelUpdate}>Cancel</button>
          </span>
        )}
      </div>
      {(error ?? reqErr) && <p role="alert" className="error">{error ?? reqErr}</p>}
    </div>
  )
}
