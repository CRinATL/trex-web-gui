import { useState, useSyncExternalStore } from 'react'
import { ApiError, putSetup, type ConnectParams, type PortView, type TestSetup } from '../../api'
import { dashboardPrefs } from '../../dashboardPrefs'
import { useHotkey } from '../../keys'
import { layerView, type LayerView } from '../../layerView'
import { pgidStore } from '../../pgidStats'
import { cpLength } from '../../portLabels'
import { buildSetup, formProblem, layerOf, slugify } from '../../setups'
import { trafficStore } from '../../trafficStore'
import { useDialogFocus } from '../useDialogFocus'

const NAME_MAX = 60   // backend store/test_setups.py NAME_MAX, in characters

// Keeps the Traffic forms of the ticked ports as a named setup. Only ports whose form has a profile are listed; one whose
// multiplier or duration the server would refuse is named with the reason and not offered.
export function SaveSetupDialog({ params, ports, setups, onClose }: { params: ConnectParams; ports: PortView[]; setups: TestSetup[]; onClose: () => void }) {
  const ref = useDialogFocus<HTMLDivElement>('[data-autofocus]')
  useHotkey('escape', onClose)
  const forms = useSyncExternalStore(trafficStore.subscribe, trafficStore.all)
  const withProfile = ports.filter((p) => forms[p.id]?.formPath)
  const problems = Object.fromEntries(withProfile.map((p) => [p.id, formProblem(forms[p.id])]))
  const [name, setName] = useState('')
  const [server, setServer] = useState(`${params.host}:${params.sync_port}`)
  // unticked by the user; a port that is valid is ticked until then
  const [off, setOff] = useState<Set<number>>(new Set())
  const [includeLayer, setIncludeLayer] = useState(false)
  const [stopFirst, setStopFirst] = useState(false)
  const [includeDash, setIncludeDash] = useState(false)
  const [overwrite, setOverwrite] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const offered = withProfile.filter((p) => problems[p.id] === null)
  const include = new Set(offered.filter((p) => !off.has(p.id)).map((p) => p.id))
  const slug = slugify(name)
  const exists = setups.some((s) => s.slug === slug)
  const nameLen = cpLength(name.trim())
  const nameProblem = nameLen === 0 ? null : slug === '' ? 'The name needs at least one letter or digit (a-z, 0-9)' : nameLen > NAME_MAX ? `The name is limited to ${NAME_MAX} characters` : null
  const canSave = !busy && nameLen > 0 && nameProblem === null && include.size > 0

  async function save() {
    const layers: Record<number, LayerView> = Object.fromEntries(ports.map((p) => [p.id, layerView(p)]))
    const dash = includeDash ? {
      prefs: dashboardPrefs.get(),
      streams: pgidStore.selection('streams').picked.map((p) => p.id), latency: pgidStore.selection('latency').picked.map((p) => p.id),
    } : null
    const setup = buildSetup(name, params, ports, forms, layers, dash, include, { includeLayer, stopFirst }, server.trim() || null)
    setBusy(true); setError(null)
    try { await putSetup(setup.slug, setup); onClose() }
    catch (x) { setError(x instanceof ApiError ? x.message : String(x)); setOverwrite(false) }
    finally { setBusy(false) }
  }

  const toggle = (id: number) => setOff((cur) => { const n = new Set(cur); if (n.has(id)) n.delete(id); else n.add(id); return n })
  return (
    <div role="dialog" aria-modal="true" aria-label="Save setup" className="modal" ref={ref}>
      <h2>Save current setup</h2>
      <div className="form-grid">
        <div className="form-row"><label htmlFor="setup-name">Name</label><input id="setup-name" data-autofocus value={name} onChange={(e) => { setName(e.target.value); setOverwrite(false) }} /></div>
        <div className="form-row"><label htmlFor="setup-server">Server</label><input id="setup-server" value={server} onChange={(e) => setServer(e.target.value)} /></div>
      </div>
      {nameProblem && <p role="status" className="error">{nameProblem}</p>}
      <fieldset>
        <legend>Ports</legend>
        {withProfile.length === 0 && <p className="muted">No port has a profile in its Traffic tab yet.</p>}
        {withProfile.map((p) => problems[p.id] === null
          ? <div key={p.id}><label><input type="checkbox" checked={include.has(p.id)} onChange={() => toggle(p.id)} /> Port {p.id}: {forms[p.id].formPath}</label>
              {includeLayer && include.has(p.id) && layerOf(layerView(p)) === null && <span className="error"> Port {p.id}: layer not included: {layerView(p).mode === 'L2' ? 'no destination MAC' : 'no addresses'}</span>}</div>
          : <div key={p.id} className="error">Port {p.id}: {problems[p.id]} ({forms[p.id].formPath}); not saved</div>)}
      </fieldset>
      <div><label><input type="checkbox" checked={includeLayer} onChange={(e) => setIncludeLayer(e.target.checked)} /> include port configuration (L2/L3/VLAN/IPv6)</label></div>
      <div><label><input type="checkbox" checked={stopFirst} onChange={(e) => setStopFirst(e.target.checked)} /> stop a transmitting port before assigning</label></div>
      <div><label><input type="checkbox" checked={includeDash} onChange={(e) => setIncludeDash(e.target.checked)} /> include dashboard layout</label></div>
      {overwrite && <p role="alert">A setup with this name already exists ({slug}). Replace it?</p>}
      {error && <p role="alert" className="error">{error}</p>}
      <div className="dialog-footer">
        <div className="end">
          <button type="button" onClick={onClose}>Cancel</button>
          {overwrite
            ? <button type="button" className="danger" disabled={busy} onClick={() => void save()}>Overwrite</button>
            : <button type="button" disabled={!canSave} onClick={() => (exists ? setOverwrite(true) : void save())}>Save</button>}
        </div>
      </div>
    </div>
  )
}
