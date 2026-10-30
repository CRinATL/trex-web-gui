import { useEffect, useRef, useState, useSyncExternalStore, type KeyboardEvent } from 'react'
import { ApiError, assign, acquire, applyLayer, deleteSetup, duplicateSetup, inspect, putSetup, renameSetup, start, stop, type ConnectParams, type SetupLoadError, type PortView, type SetupPort, type TestSetup } from '../../api'
import { useHotkey } from '../../keys'
import { setupSession, useRunInProgress, useSetupSession } from '../../setupSession'
import { CHANGED_SINCE_LOAD, buildSetup, fingerprint, formDrift, formProblem, loadSetup, matchPorts, runSetup, staleReason, type LoadResult, type StepEvent } from '../../setups'
import { DEFAULT_FORM, trafficStore } from '../../trafficStore'
import { useDialogFocus } from '../useDialogFocus'
import { RunButton } from '../RunButton'
import { EditedTag, unsavedText } from '../../ui/Edited'

const errText = (x: unknown) => (x instanceof ApiError || x instanceof Error ? x.message : String(x))
// What Run will do to a port beyond assigning its profile and starting it, so nothing happens that the table did not say; '—' for nothing.
const runEffects = (p: SetupPort): string => {
  const e = [p.acquire_force && 'force-acquire', p.stop_first && 'stops a transmitting port first', p.layer && 'applies a port configuration']
  const on = e.filter(Boolean)
  return on.length ? on.join(', ') : '—'
}
// 'Saved for' wants a local date and time; a stamp the browser cannot parse is shown as stored.
const localTime = (iso: string): string => { const d = new Date(iso); return Number.isNaN(d.getTime()) ? iso : d.toLocaleString() }
// The file name of a profile path, for the table; the full path stays in the cell's title.
const baseName = (path: string): string => path.split(/[\\/]/).pop() || path
type Edit = { kind: 'rename' | 'duplicate'; slug: string; name: string }

// Lists the saved setups; Load inspects every profile (and so runs a .py profile, which is why each path is shown first),
// Run applies a loaded setup in phases. The Load and Run results live here: a closed dialog forgets them.
export function SetupDialog({ setups, loadErrors, params, ports, readOnly, onClose }: {
  setups: TestSetup[]; loadErrors: SetupLoadError[]; params: ConnectParams | null; ports: PortView[]; readOnly: boolean; onClose: () => void
}) {
  const ref = useDialogFocus<HTMLDivElement>('[data-autofocus]')
  useHotkey('escape', onClose)
  const running = useRunInProgress()
  const { setup: loadedSetup, loaded, status, result, error } = useSetupSession()
  const [busy, setBusy] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const [forcing, setForcing] = useState<number | null>(null)
  const [edit, setEdit] = useState<Edit | null>(null)
  const [phase, setPhase] = useState<string | null>(null)   // Save and Run: Saving…, Loading…, until the Run begins
  const forms = useSyncExternalStore(trafficStore.subscribe, trafficStore.all)
  // The ports as of the latest render, for runSetup to read when it needs them (not the render that clicked Run).
  const portsRef = useRef(ports)
  useEffect(() => { portsRef.current = ports })
  const [editError, setEditError] = useState<string | null>(null)   // a failed Rename, Duplicate or Delete: belongs to the selection it was made on
  const setError = (e: string | null) => setupSession.set({ error: e })

  // The setups as of the latest render, for runSetup to compare with the Load when it starts.
  const setupsRef = useRef(setups)
  useEffect(() => { setupsRef.current = setups })
  // The Load that Run may use: a good one of this setup, whose content the list still holds. Run executes the Loaded snapshot.
  const loadedFor = (s: TestSetup) => !!loaded?.ok && loadedSetup?.slug === s.slug
  const stale = (s: TestSetup) => loadedFor(s) && staleReason(loadedSetup as TestSetup, loaded as NonNullable<typeof loaded>, setups) !== null
  const runnable = (s: TestSetup) => loadedFor(s) && !stale(s)
  const blocked = readOnly ? 'read-only session' : !params ? 'Connect first' : running ? 'a Run is in progress' : busy ? 'busy' : null
  const shown = loadedSetup ? setups.find((s) => s.slug === loadedSetup.slug) ?? null : null
  // The selected setup: the user's pick while the list still holds it, else the loaded one, else the first.
  const [pick, setPick] = useState<string | null>(null)
  const sel = setups.find((s) => s.slug === pick) ?? shown ?? setups[0] ?? null
  // Only the setup the session holds has Load results, drift and a Run result; any other shows its saved content.
  const active = !!sel && shown?.slug === sel.slug
  const listRef = useRef<HTMLUListElement>(null)

  // Status is keyed by the setup port's index. A Load or Run writes through `update` with the epoch it started in, so one that
  // outlives a connect_seq reset reports into nothing.
  async function doLoad(s: TestSetup) {
    if (!params) return
    const epoch = setupSession.epoch()
    const put = (p: Parameters<typeof setupSession.update>[1]) => setupSession.update(epoch, p)
    setBusy(true); setForcing(null)
    put({ error: null, result: null, loaded: null, setup: s, status: {} })
    try {
      const r = await loadSetup(s, params, ports, { inspect })
      put({ loaded: r, status: Object.fromEntries(r.ports.map((p, i) => [i, p.error ?? 'ready'])) })
    } catch (x) { put({ error: errText(x), status: {} }) }
    finally { setBusy(false) }
  }

  // `fresh` is a Load made a moment ago by Save and Run: the session and the list prop have not caught up with it yet.
  // `before` is the fingerprint of the setup as it was before the save: the list may stand in for the saved copy only while it still holds that.
  async function doRun(resume?: { acquired: number[]; force: number }, fresh?: { setup: TestSetup; load: LoadResult; before: string }) {
    const load = fresh?.load ?? loaded, snapshot = fresh?.setup ?? loadedSetup
    if (!load || !snapshot) return
    const listNow = () => {
      const list = setupsRef.current
      if (!fresh) return list
      const held = list.find((x) => x.slug === fresh.setup.slug)
      return held && fingerprint(held) === fresh.before ? list.map((x) => (x === held ? fresh.setup : x)) : list
    }
    const epoch = setupSession.epoch()
    const put = (p: Parameters<typeof setupSession.update>[1]) => setupSession.update(epoch, p)
    const indexes = (ids: number[]) => load.ports.flatMap((p, i) => (ids.includes(p.id) ? [i] : []))
    setBusy(true); setForcing(null)
    put({ error: null, ...(resume ? {} : { status: {}, result: null }) })
    const label: Record<StepEvent['step'], string> = { acquire: 'acquired', stop: 'stopped', layer: 'layer applied', assign: 'profile assigned', start: 'started' }
    const onStep = (e: StepEvent) => {
      if (e.phase === 'begin') return
      put((cur) => ({ status: { ...cur.status, ...Object.fromEntries(indexes(e.ports).map((i) => [i, label[e.step]])) } }))
    }
    try {
      const r = await runSetup(snapshot, load, { ports: () => portsRef.current, current: listNow, acquire, stop, applyLayer, assign, start }, onStep, resume)
      const f = r.failed
      put((cur) => ({ result: r, ...(f ? { status: { ...cur.status, ...Object.fromEntries(indexes([f.id]).map((i) => [i, `failed at ${f.step}`])) } } : {}) }))
    } catch (x) { put({ error: errText(x) }) }
    finally { setBusy(false) }
  }

  // The Loaded setup with only the Traffic-form fields taken from the forms now; everything the forms do not hold stays as saved.
  const savedFrom = (s: TestSetup): TestSetup => {
    const built = buildSetup(s.name, params as ConnectParams, ports, forms, {}, null, new Set(rows.flatMap((m) => (m.view ? [m.view.id] : []))), { includeLayer: false, stopFirst: false }, s.server)
    return {
      ...s, ports: s.ports.map((p, i) => {
        const b = built.ports.find((x) => x.port === rows[i].view?.id)
        return b ? { ...p, profile: b.profile, tunables: b.tunables, mult: b.mult, duration: b.duration, force: b.force } : p
      }),
    }
  }
  // Why the forms cannot be saved, or null: the first loaded port whose form has a problem.
  const formsProblem = (): string | null => {
    for (const m of rows) {
      const f = m.view ? forms[m.view.id] ?? DEFAULT_FORM : null
      if (m.view && f) { const why = !f.formPath ? 'no profile' : formProblem(f); if (why) return `Port ${m.view.id}: ${why}` }
    }
    return null
  }

  async function doSaveAndRun() {
    if (!params || !loadedSetup) return
    const problem = formsProblem()
    if (problem) { setError(`Fix the form first: ${problem}`); return }
    const epoch = setupSession.epoch(), before = fingerprint(loadedSetup)
    const put = (p: Parameters<typeof setupSession.update>[1]) => setupSession.update(epoch, p)
    setBusy(true); setForcing(null)
    put({ error: null })
    try {
      setPhase('Saving…')
      const saved = await putSetup(loadedSetup.slug, savedFrom(loadedSetup))
      setPhase('Loading…')
      put({ result: null, status: {} })
      const r = await loadSetup(saved, params, ports, { inspect })
      put({ setup: saved, loaded: r, status: Object.fromEntries(r.ports.map((p, i) => [i, p.error ?? 'ready'])) })
      if (r.ok) { setPhase(null); await doRun(undefined, { setup: saved, load: r, before }) }
    } catch (x) { put({ error: errText(x) }) }
    finally { setPhase(null); setBusy(false) }
  }

  async function doStopStarted() {
    if (!result?.started.length || !loaded) return
    const epoch = setupSession.epoch()
    const started = result.started
    setBusy(true); setError(null)
    try {
      await stop({ ports: started })
      setupSession.update(epoch, (cur) => ({
        status: { ...cur.status, ...Object.fromEntries(loaded.ports.flatMap((p, i) => (started.includes(p.id) ? [[i, 'stopped']] : []))) },
        result: cur.result && { ...cur.result, started: [] },
      }))
    } catch (x) { setupSession.update(epoch, { error: errText(x) }) }
    finally { setBusy(false) }
  }

  async function doDelete(slug: string) {
    setConfirmDelete(null); setBusy(true); setEditError(null)
    try { await deleteSetup(slug); if (loadedSetup?.slug === slug) setupSession.reset() }
    catch (x) { setEditError(errText(x)) }
    finally { setBusy(false) }
  }

  async function doEdit() {
    if (!edit) return
    setBusy(true); setEditError(null)
    try {
      const saved = await (edit.kind === 'rename' ? renameSetup : duplicateSetup)(edit.slug, edit.name)
      setEdit(null)
      if (edit.kind === 'rename') setPick(saved.slug)   // the selection follows the renamed setup
    }
    catch (x) { setEditError(errText(x)) }
    finally { setBusy(false) }
  }

  const failed = active ? result?.failed ?? null : null
  // A failed Run brings the failed port's row into view: it would otherwise sit under the sticky Load/Run bar.
  const portsTable = useRef<HTMLTableElement>(null)
  const failedId = failed?.id
  useEffect(() => {
    if (failedId === undefined) return
    const row = portsTable.current?.querySelector(`tr[data-port="${failedId}"]`)
    if (row && typeof row.scrollIntoView === 'function') row.scrollIntoView({ block: 'nearest' })   // jsdom has no scrollIntoView
  }, [failedId, result])
  const rows = active && loadedSetup ? matchPorts(loadedSetup, params ?? { host: '', sync_port: 0, async_port: 0, timeout_s: 0 }, ports) : []
  // Each port of a good Load whose form has changed since Load wrote it. Empty until a Load has succeeded.
  const driftLoaded = loaded?.ok && shown ? loaded.ports.flatMap((p) => {
    const was = loaded.forms[p.id]
    const d = was ? formDrift(was, forms[p.id] ?? DEFAULT_FORM) : []
    return d.length ? [`Port ${p.id}: ${d.join(', ')} — not used by Run`] : []
  }) : []
  const drift = active ? driftLoaded : []   // the detail shows drift only for the setup the session holds
  const problem = drift.length ? formsProblem() : null
  const saveBlocked = blocked ?? (!sel || !runnable(sel) ? 'the setup changed since it was loaded' : problem ? `fix the form first: ${problem}` : null)
  // The table rows: the matched ports once this setup is the loaded one, else the saved ports with nothing matched yet.
  const tableRows = active ? rows : (sel?.ports ?? []).map((port) => ({ port, view: null, reason: null as string | null }))

  // Up/Down/Home/End move the selection along the setups (the load-error options are disabled and not in `setups`);
  // the selection follows the key and the new option takes focus, so there is one Tab stop.
  // Changing the selection drops the per-setup state: the open Rename/Duplicate row, a pending delete or force-acquire
  // confirm, none of which belong to the setup now shown. The session's last Load/Run error stays, and shows only while its setup is the one selected.
  function select(slug: string) {
    if (slug === sel?.slug) return
    setPick(slug); setEdit(null); setConfirmDelete(null); setForcing(null); setEditError(null)
  }
  function onListKey(e: KeyboardEvent<HTMLUListElement>) {
    if (!sel) return
    const at = setups.indexOf(sel)
    const to = e.key === 'ArrowDown' ? Math.min(at + 1, setups.length - 1) : e.key === 'ArrowUp' ? Math.max(at - 1, 0)
      : e.key === 'Home' ? 0 : e.key === 'End' ? setups.length - 1 : -1
    if (to < 0) return
    e.preventDefault()
    select(setups[to].slug)
    listRef.current?.querySelector<HTMLElement>(`[data-slug="${setups[to].slug}"]`)?.focus()
  }
  // One option's muted line: '<server> · <n> port(s)', then ' · loaded' and ', <k> edit(s)' for the loaded setup.
  const option = (s: TestSetup) => {
    const k = loadedFor(s) ? driftLoaded.length : 0
    return `${s.server ?? 'any server'} · ${s.ports.length} port${s.ports.length === 1 ? '' : 's'}${loadedFor(s) ? ' · loaded' : ''}${k ? `, ${k} edit${k === 1 ? '' : 's'}` : ''}`
  }
  // A Rename name that differs from the setup's is marked edited (RC1-31); a Duplicate's name is a new setup's, compared with nothing.
  const renamed = !!edit && !!sel && edit.kind === 'rename' && edit.name !== sel.name
  const noteWhy = sel && !runnable(sel) && !blocked ? (stale(sel) ? `${CHANGED_SINCE_LOAD}.` : 'Load first: Run uses the profiles a Load inspected.') : null
  return (
    <div role="dialog" aria-modal="true" aria-label="Test setups" className="modal setups-dialog" ref={ref}>
      <h2>Test setups</h2>
      {setups.length === 0 && loadErrors.length === 0
        ? <p className="muted">No saved setups. Use Setups ▾ → Save current… on the Control page.</p>
        : (
          <div className="setups-master">
            <ul role="listbox" aria-label="Setups" className="setups-list" ref={listRef} onKeyDown={onListKey}>
              {setups.map((s) => (
                <li key={s.slug} role="option" aria-selected={s === sel} data-slug={s.slug} tabIndex={s === sel ? 0 : -1}
                  data-autofocus={s === sel ? '' : undefined} onClick={() => select(s.slug)}>
                  <span className="setup-name">{s.name}</span>{' '}
                  <span className="muted setup-meta">{option(s)}</span>
                </li>
              ))}
              {loadErrors.map((e) => (
                <li key={e.file} role="option" aria-selected="false" aria-disabled="true" className="setup-bad">
                  <span className="error">{e.file}: {e.error}</span>
                </li>
              ))}
            </ul>
            {sel && (
              <section aria-label={sel.name} className="setups-detail">
                <div className="setups-heading">
                  <h3>{sel.name}</h3>
                  <div className="end">
                    <button type="button" disabled={busy || running || !!edit} onClick={() => setEdit({ kind: 'rename', slug: sel.slug, name: sel.name })}>Rename</button>
                    <button type="button" disabled={busy || running || !!edit} onClick={() => setEdit({ kind: 'duplicate', slug: sel.slug, name: `${sel.name} copy` })}>Duplicate</button>
                    <button type="button" disabled={busy || running || !!edit} onClick={() => setConfirmDelete(sel.slug)}>Delete</button>
                  </div>
                </div>
                {confirmDelete === sel.slug && (
                  <p role="alert">Delete setup {sel.name}? <button type="button" className="danger" onClick={() => void doDelete(sel.slug)}>Confirm delete</button>{' '}
                    <button type="button" onClick={() => setConfirmDelete(null)}>Cancel</button></p>
                )}
                {edit && (
                  <p role="group" aria-label={`${edit.kind === 'rename' ? 'Rename' : 'Duplicate'} setup`}>
                    <label>New name <input value={edit.name} className={renamed ? 'edited' : undefined} aria-describedby={renamed ? 'setup-rename-ed' : undefined}
                      onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></label>{renamed && <EditedTag id="setup-rename-ed" />}{' '}
                    <button type="button" disabled={busy || !edit.name.trim()} onClick={() => void doEdit()}>{edit.kind === 'rename' ? 'Rename' : 'Duplicate'}</button>{' '}
                    <button type="button" onClick={() => setEdit(null)}>Cancel</button>
                    {renamed && <>{' '}<span className="unsaved-count">{unsavedText(1)}</span></>}
                  </p>
                )}
                <p className="muted">Saved for {sel.server ?? 'any server'} · updated {localTime(sel.updated)}</p>
                <table aria-label="Ports" className="setups-ports" ref={portsTable}>
                  <thead><tr><th scope="col">Port</th><th scope="col">Profile</th><th scope="col">Rate</th><th scope="col">On Run</th><th scope="col">Status</th></tr></thead>
                  <tbody>
                    {tableRows.map((m, i) => {
                      const text = active ? status[i] ?? m.reason ?? 'not loaded' : 'not loaded'
                      return (
                        <tr key={m.port.port} data-port={m.view?.id ?? m.port.port}>
                          <td>{m.view?.id ?? m.port.port}</td>
                          <td title={m.port.profile.path}>{baseName(m.port.profile.path)}</td>
                          <td>{m.port.mult}</td>
                          <td>{runEffects(m.port)}</td>
                          {/* the row says only where the run stopped; the reason is in the result line below (and this cell's tooltip) */}
                          <td className={text.startsWith('failed') ? 'error' : undefined} title={text.startsWith('failed') ? failed?.message : undefined}>{text}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
                {drift.length === 0
                  ? <p className="muted">Run uses the setup as saved.</p>
                  : (
                    <div role="status">
                      <p>Edited in the Traffic tab since Load. Run uses the setup as saved; Save and Run saves these edits first.</p>
                      <ul aria-label="Edits since Load">{drift.map((d) => <li key={d}>{d}</li>)}</ul>
                      {saveBlocked && <p>Save and Run is unavailable: {saveBlocked}.</p>}
                    </div>
                  )}
                {active && phase && <p role="status">{phase}</p>}
                {active && loaded?.serverMismatch && <p role="status" className="error">{loaded.serverMismatch}</p>}
                {active && result?.warnings.map((w) => <p key={w} role="status" className="error">{w}</p>)}
                {active && error && <p role="alert" className="error">{error}</p>}
                {editError && <p role="alert" className="error">{editError}</p>}
                <div className="setups-actions">
                  <div className="setups-buttons">
                    <button type="button" disabled={busy || running || !params} onClick={() => void doLoad(sel)}>Load</button>
                    <button type="button" disabled={blocked !== null || !runnable(sel)} onClick={() => void doRun()}>{drift.length ? 'Run as saved' : 'Run'}</button>
                    {drift.length > 0 && <button type="button" disabled={saveBlocked !== null} onClick={() => void doSaveAndRun()}>Save and Run</button>}
                    {blocked && !running && <span className="muted">{blocked === 'Connect first' ? blocked : `Run is unavailable: ${blocked}.`}</span>}
                    {noteWhy && <span className="muted">{noteWhy}</span>}
                  </div>
                  {failed?.step === 'acquire' && failed.code === 'not_owned' && forcing === null && !running && (
                    <button type="button" disabled={busy || blocked !== null} onClick={() => setForcing(failed.id)}>{`Force-acquire port ${failed.id} and continue`}</button>
                  )}
                  {active && forcing !== null && (
                    <p role="alert">Take port {forcing} from its current owner? <button type="button" disabled={busy}
                      onClick={() => void doRun({ acquired: result?.acquired ?? [], force: forcing })}>Force-acquire</button>{' '}
                      <button type="button" onClick={() => setForcing(null)}>Cancel</button></p>
                  )}
                  {failed && result && result.started.length > 0 && <RunButton kind="stop" disabled={busy || running} onClick={() => void doStopStarted()}>Stop started ports</RunButton>}
                  {active && result && (failed
                    ? <p role="status">Run stopped at port {failed.id}: failed at {failed.step}: {failed.message.replace(/\.$/, '')}.</p>
                    : <p role="status">Run finished: started port{result.started.length === 1 ? '' : 's'} {result.started.join(', ')}.</p>)}
                </div>
              </section>
            )}
          </div>
        )}
      {!sel && (error ?? editError) && <p role="alert" className="error">{error ?? editError}</p>}
      <div className="dialog-footer"><div className="end"><button type="button" data-autofocus={sel ? undefined : ''} onClick={onClose}>Close</button></div></div>
    </div>
  )
}
