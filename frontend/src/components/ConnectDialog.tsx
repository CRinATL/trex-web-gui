import { useEffect, useRef, useState } from 'react'
import { ApiError, connect, getSettings, putSettings, type ConnectParams, type SavedConnection, type ServerInfo, type Settings } from '../api'
import { useHotkey } from '../keys'
import { monitorStore } from '../monitorStore'
import { useAppState } from '../state'
import { stfRunOn, stfRunText } from '../stfDetect'
import { EditedTag, unsavedText } from '../ui/Edited'
import { Info } from '../ui/Info'
import { OpenMonitorLink } from './OpenMonitorLink'
import { useDialogFocus } from './useDialogFocus'

interface Form { host: string; sync_port: string; async_port: string; timeout_s: string }
// True for the ports and timeout a stock TRex server answers on; anything else opens the Advanced fold.
const isDefault = (f: Form): boolean => f.sync_port === '4501' && f.async_port === '4500' && f.timeout_s === '3'
const toForm = (c: { host: string; sync_port: number; async_port: number; timeout_s: number }): Form =>
  ({ host: c.host, sync_port: String(c.sync_port), async_port: String(c.async_port), timeout_s: String(c.timeout_s) })

// Which advanced field a validation message is about, or null for the Host message.
function advancedField(msg: string): 'sync_port' | 'async_port' | 'timeout_s' | null {
  return msg.startsWith('Command port') ? 'sync_port' : msg.startsWith('Event port') ? 'async_port' : msg.startsWith('Timeout') ? 'timeout_s' : null
}

// eslint-disable-next-line react-refresh/only-export-components
export function validate(f: Form): string | null {
  if (!f.host.trim()) return 'Host is required'
  for (const [label, v] of [['Command port', f.sync_port], ['Event port', f.async_port]] as const) {
    const n = Number(v)
    if (!/^\d+$/.test(v) || n < 1 || n > 65535) return `${label} must be 1–65535`
  }
  const t = Number(f.timeout_s)
  if (!(t >= 0.5 && t <= 60)) return 'Timeout must be 0.5–60 s'
  return null
}

export function ConnectDialog({ initialHost, initialParams, initialPorts, onConnected, onClose }: { initialHost: string; initialParams?: ConnectParams | null; initialPorts?: { sync_port: number; async_port: number } | null; onConnected?: (i: ServerInfo) => void; onClose: () => void }) {
  const [form, setForm] = useState<Form>(toForm(initialParams ?? { host: initialHost, sync_port: initialPorts?.sync_port ?? 4501, async_port: initialPorts?.async_port ?? 4500, timeout_s: 3 }))
  const [settings, setSettings] = useState<Settings | null>(null)
  const [advanced, setAdvanced] = useState(() => !isDefault(form))   // the fold is open when the ports are not the stock ones
  const [naming, setNaming] = useState<string | null>(null)   // the inline Save as… name, null while it is not asked for
  const [savingName, setSavingName] = useState(false)   // an inline save is in flight
  const [focusField, setFocusField] = useState<{ field: 'sync_port' | 'async_port' | 'timeout_s' } | null>(null)   // a fresh object per request, so the same field is focused again
  const saveAsRef = useRef<HTMLButtonElement>(null)
  const [picked, setPicked] = useState('')
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [stfHost, setStfHost] = useState<string | null>(null)   // set when the connect failed on a host that runs a stateful (-f) run
  const [busy, setBusy] = useState(false)
  const [readOnly, setReadOnly] = useState(initialParams?.read_only ?? false)   // per-connect choice; deliberately not part of SavedConnection
  const [settingsError, setSettingsError] = useState<string | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const inFlight = useRef(false)
  const ref = useDialogFocus<HTMLDivElement>('[data-autofocus]')
  // while a connect is in flight the dialog must stay open so its result or error is shown
  const close = () => { if (!inFlight.current) onClose() }
  useHotkey('escape', close)
  // The settings the app already has (a settings message, so an import shows at once) win over a fetch; the dialog keeps its own copy for editing.
  const { settings: live, daemon, lastStfRun } = useAppState()
  const [seenLive, setSeenLive] = useState<typeof live>(null)   // null, so a value present at mount is taken on the first render
  if (live !== seenLive) {   // set during render, React's pattern for state that follows a changing input
    setSeenLive(live)
    if (live) {
      setSettings(live.settings); setLoadError(live.load_error)
      if (!live.settings.connections.some((c) => c.name === picked)) { setPicked(''); setConfirmDelete(false) }   // an import can drop it
    }
  }
  const liveSeen = useRef(false)
  useEffect(() => { if (live) liveSeen.current = true }, [live])
  useEffect(() => {
    if (live) return   // already have them; a message that comes while the fetch runs is not overwritten (liveSeen below)
    getSettings()
      .then((r) => { if (!liveSeen.current) { setSettings(r.settings); setLoadError(r.load_error) } })   // a message that came meanwhile is newer
      .catch((x) => setSettingsError(x instanceof ApiError ? x.message : String(x)))
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, on open
  }, [])

  // Shows a validation message; one about an Advanced field opens the fold and moves focus to that field once it is shown.
  function reject(v: string) {
    setError(v)
    const f = advancedField(v)
    if (f) { setAdvanced(true); setFocusField({ field: f }) }
  }
  useEffect(() => {
    if (!focusField) return
    const id = { sync_port: 'connect-sync', async_port: 'connect-async', timeout_s: 'connect-timeout' }[focusField.field]
    ref.current?.querySelector<HTMLInputElement>(`#${id}`)?.focus()
  }, [focusField, ref])
  // Closes the inline name row and returns focus to Save as…, which stays mounted.
  function closeNaming() { setNaming(null); saveAsRef.current?.focus() }

  // A field is marked edited (RC1-31) when Save as… would store another value than the picked saved connection holds, by the
  // comparison params() makes: the host trimmed, the ports and timeout as numbers, so 3.0 over 3 is not an edit (R6). With
  // none picked nothing is marked.
  const savedConn = picked ? settings?.connections.find((c) => c.name === picked) : undefined
  const isEdited = (k: keyof Form) => savedConn !== undefined && (k === 'host' ? form.host.trim() !== savedConn.host : Number(form[k]) !== savedConn[k])
  const marks = (k: keyof Form) => (isEdited(k) ? { className: 'edited', 'aria-describedby': `connect-${k}-ed` } : {})
  const tag = (k: keyof Form) => isEdited(k) && <EditedTag id={`connect-${k}-ed`} />
  const unsaved = unsavedText((['host', 'sync_port', 'async_port', 'timeout_s'] as const).filter(isEdited).length)
  const set = (k: keyof Form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value })
  const params = () => ({ host: form.host.trim(), sync_port: Number(form.sync_port), async_port: Number(form.async_port), timeout_s: Number(form.timeout_s) })

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (inFlight.current) return
    const v = validate(form)
    if (v) { reject(v); return }
    inFlight.current = true; setBusy(true); setError(null); setStfHost(null)
    try { const r = await connect({ ...params(), read_only: readOnly }); onConnected?.(r.info); onClose() }
    catch (x) {
      // An STF run has no command port, so the plain error would send the user hunting for a fault: say what it is (spec 13.5).
      const host = form.host.trim()
      if (x instanceof ApiError && x.code === 'unreachable' && stfRunOn(host, { daemon, monitor: monitorStore.get(), lastStfRun, now: Date.now() })) setStfHost(host)
      else setError(x instanceof ApiError ? x.message : String(x))
    }
    finally { inFlight.current = false; setBusy(false) }
  }

  async function save(conns: SavedConnection[]): Promise<boolean> {
    if (!settings) return false
    try { const r = await putSettings({ ...settings, connections: conns }); setSettings(r.settings); setLoadError(r.load_error); return true }
    catch (x) { setError(x instanceof ApiError ? x.message : String(x)); return false }
  }

  async function confirmDeletion() {
    setConfirmDelete(false)
    if (await save(settings!.connections.filter((c) => c.name !== picked))) setPicked('')
  }

  function pick(name: string) {
    setPicked(name); setConfirmDelete(false)
    const c = settings?.connections.find((x) => x.name === name)
    if (c) { const f = toForm(c); setForm(f); if (!isDefault(f)) setAdvanced(true) }
  }

  // Opens the inline name field, after the form passes validation; the name starts as the picked one or the host.
  function askName() {
    const v = validate(form); if (v) { reject(v); return }
    setNaming(picked || form.host)
  }

  // Saves the form under the typed name, replacing a saved connection of that name; a success also clears an old connect error.
  async function saveAs() {
    const name = naming?.trim()
    if (!name || !settings || savingName) return
    const v = validate(form); if (v) { reject(v); return }   // the form may have been edited since the row opened
    // names are unique case-insensitively after strip (backend rule), so replace on that key
    const key = name.toLowerCase()
    setSavingName(true)
    const ok = await save([...settings.connections.filter((c) => c.name.trim().toLowerCase() !== key), { name, ...params() }])
    setSavingName(false)
    if (ok) { setPicked(name); closeNaming(); setError(null) }
    else ref.current?.querySelector<HTMLInputElement>('#connect-name')?.focus()   // a click on Save moved focus off the field
  }

  return (
    <div className="modal" role="dialog" aria-modal="true" aria-labelledby="connect-title" ref={ref}>
      <form onSubmit={submit} noValidate>
        <h2 id="connect-title">Connect to TRex</h2>
        <div className="form-grid">
          <div className="form-row">
            <label htmlFor="connect-saved">Saved</label>
            <span>
              <select id="connect-saved" value={picked} onChange={(e) => pick(e.target.value)}>
                <option value="">—</option>
                {settings?.connections.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
              </select>
              {picked && (confirmDelete
                ? <button type="button" className="danger" onClick={() => void confirmDeletion()}>Confirm delete</button>
                : <button type="button" disabled={!settings} onClick={() => setConfirmDelete(true)}>Delete</button>)}
            </span>
          </div>
          <div className="form-row"><label htmlFor="connect-host">Host</label><span className="stretch-field"><input id="connect-host" {...marks('host')} value={form.host} onChange={set('host')} data-autofocus />{tag('host')}</span></div>
          <div className="form-row"><label htmlFor="connect-observer">Read-only (observer)</label><input id="connect-observer" type="checkbox" checked={readOnly} onChange={(e) => setReadOnly(e.target.checked)} /></div>
          <details className="fold full" open={advanced} onToggle={(e) => setAdvanced(e.currentTarget.open)}>
            <summary>{advanced ? 'Advanced' : `Advanced · ports ${form.sync_port} / ${form.async_port}, timeout ${form.timeout_s} s`}</summary>
            <div className="form-grid">
              <div className="form-row">
                <label htmlFor="connect-sync">Command port</label>
                <span><input id="connect-sync" {...marks('sync_port')} value={form.sync_port} onChange={set('sync_port')} inputMode="numeric" />{tag('sync_port')}
                  <Info label="the ports">Set on the TRex server by <code>zmq_rpc_port</code> and <code>zmq_pub_port</code> in its <code>trex_cfg.yaml</code>; 4501 and 4500 when not set. Change them only when the server's config does.</Info></span>
              </div>
              <div className="form-row"><label htmlFor="connect-async">Event port</label><span className="stretch-field"><input id="connect-async" {...marks('async_port')} value={form.async_port} onChange={set('async_port')} inputMode="numeric" />{tag('async_port')}</span></div>
              <div className="form-row"><label htmlFor="connect-timeout">Timeout (s)</label><span className="stretch-field"><input id="connect-timeout" {...marks('timeout_s')} value={form.timeout_s} onChange={set('timeout_s')} inputMode="decimal" />{tag('timeout_s')}</span></div>
            </div>
          </details>
        </div>
        {loadError && <p role="status" className="muted">The settings file could not be read, so defaults are shown. Saving is allowed: the bad file is first kept as settings.toml.bad-&lt;UTC&gt;Z.</p>}
        {naming !== null && (
          <div className="form-grid" role="group" aria-label="Save as">
            <div className="form-row">
              <label htmlFor="connect-name">Name</label>
              <span>
                <input id="connect-name" value={naming} onChange={(e) => setNaming(e.target.value)} autoFocus
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') { e.preventDefault(); void saveAs() }
                    else if (e.key === 'Escape') { e.stopPropagation(); e.nativeEvent.stopImmediatePropagation(); closeNaming() }   // cancels the row only, as Info does
                  }} />
                <button type="button" disabled={!naming.trim() || savingName} onClick={() => void saveAs()}>Save</button>
                <button type="button" aria-label="Cancel save as" onClick={closeNaming}>Cancel</button>
              </span>
            </div>
          </div>
        )}
        {settingsError && <p role="alert" className="error">{settingsError}</p>}
        {error && <p role="alert" className="error">{error}</p>}
        {stfHost && <p role="alert" className="error">{stfRunText(stfHost)} <OpenMonitorLink onNavigate={onClose} /></p>}
        <div className="dialog-footer">
          <button type="button" ref={saveAsRef} disabled={!settings} onClick={askName}>Save as…</button>
          {unsaved && <span className="unsaved-count">{unsaved}</span>}
          <div className="end">
            <button type="button" disabled={busy} onClick={close}>Cancel</button>
            <button type="submit" data-run="go" disabled={busy}>{busy ? 'Connecting…' : 'Connect'}</button>
          </div>
        </div>
      </form>
    </div>
  )
}
