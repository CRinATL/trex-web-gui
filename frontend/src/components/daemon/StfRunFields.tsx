import { useEffect, useState } from 'react'
import { ApiError, daemonStfProfile, daemonStfProfiles, type StfProfileInfo } from '../../api'
import { Info } from '../../ui/Info'
import { HOST_FALLBACK, useHost } from '../../useHost'
import { BrowseDialog } from '../BrowseDialog'
import { clampDuration, monitorPortPrefill, STF_D_MAX, STF_D_MIN, STF_L_MAX, STF_M_MAX, STF_RX_CHECK_MAX, stfProblems, type PushedConfig, type ServerPorts, type StfForm } from './daemonForm'
import { StfUploadDialog, type UploadTarget } from './StfUploadDialog'

const errText = (x: unknown) => (x instanceof ApiError || x instanceof Error ? x.message : String(x))

type Source = 'stock' | 'uploaded'

// The Run tab's fields for a stateful run (spec 13.3): the profile (a stock one, or one uploaded to the daemon, spec 13.7) with what
// the daemon reads of it, then the typed fields and the Monitor port. `onValid` reports whether the whole form can be started; the
// profile must be one the daemon read without errors. `upload` is where Upload… sends files (null: not connected, so it is disabled);
// `onModal` tells the dialog that one of the upload dialogs is open (so Escape closes only that).
export function StfRunFields({ form, onChange, cfgPath, pushed, serverPorts, onValid, upload, onModal }: {
  form: StfForm; onChange: (patch: Partial<StfForm>) => void
  cfgPath: string; pushed: PushedConfig | null; serverPorts: ServerPorts
  onValid: (ok: boolean) => void
  upload: UploadTarget | null; onModal: (open: boolean) => void
}) {
  const host = useHost()
  const [source, setSource] = useState<Source>(form.profile.startsWith('/') ? 'uploaded' : 'stock')
  const [listVersion, setListVersion] = useState(0)   // a new upload re-reads the uploaded list
  const [browsing, setBrowsing] = useState(false)
  const [uploadPath, setUploadPath] = useState<string | null>(null)
  const [files, setFiles] = useState<string[] | null>(null)
  const [listError, setListError] = useState<string | null>(null)
  const [info, setInfo] = useState<StfProfileInfo | null>(null)
  const [infoError, setInfoError] = useState<{ path: string; message: string } | null>(null)
  const shown = info?.path === form.profile ? info : null   // an answer for the previously picked profile is not this one's
  const shownError = infoError?.path === form.profile ? infoError.message : null
  const monitorPort = form.monitorPort ?? String(monitorPortPrefill(cfgPath, pushed, serverPorts))
  const problems = stfProblems(form, monitorPort)

  useEffect(() => {
    let live = true
    daemonStfProfiles(source).then((r) => { if (live) setFiles(r.files) }, (x) => { if (live) setListError(errText(x)) })
    return () => { live = false }
  }, [source, listVersion])
  useEffect(() => { onModal(browsing || uploadPath !== null) }, [browsing, uploadPath, onModal])
  useEffect(() => () => onModal(false), [onModal])
  function pickSource(next: Source) {
    if (next === source) return
    setSource(next); setFiles(null); setListError(null); onChange({ profile: '' })
  }
  // The new profile is selected, and the uploaded list is read again to include it.
  function uploaded(profile: string) {
    setUploadPath(null); setSource('uploaded'); setFiles(null); setListError(null); setListVersion((v) => v + 1)
    onChange({ profile })
  }
  // the picked profile stays selectable while the list (read before an upload) does not show it yet
  const options = files && form.profile && !files.includes(form.profile) && (source === 'uploaded') === form.profile.startsWith('/') ? [...files, form.profile] : files
  // The picked profile: read what the daemon makes of it, and prefill Duration from its YAML.
  useEffect(() => {
    if (!form.profile) return
    let live = true
    daemonStfProfile(form.profile).then((r) => {
      if (!live) return
      setInfo(r)
      if (typeof r.duration === 'number' && Number.isFinite(r.duration)) onChange({ d: String(clampDuration(r.duration)) })
    }, (x) => { if (live) setInfoError({ path: form.profile, message: errText(x) }) })
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only a new pick fetches; onChange is the parent's setter
  }, [form.profile])
  const ok = !!form.profile && shown !== null && shown.errors.length === 0 && Object.keys(problems).length === 0
  useEffect(() => { onValid(ok) }, [ok, onValid])

  const field = (id: string, label: string, key: keyof StfForm, value: string, bad: string | undefined, bounds: { min: number; max: number; step?: string }, extra?: React.ReactNode) => (
    <div className="form-row">
      <label htmlFor={id}>{label}</label>
      <span>
        <input id={id} type="number" className="narrow" value={value} min={bounds.min} max={bounds.max} step={bounds.step ?? '1'}
          aria-invalid={bad ? true : undefined} aria-describedby={bad ? `${id}-err` : undefined} onChange={(e) => onChange({ [key]: e.target.value })} />
        {extra}
        {bad && <span id={`${id}-err`} className="error"> {bad}</span>}
      </span>
    </div>
  )

  return (
    <>
    <div className="form-grid stf-fields" role="group" aria-label="Stateful run">
      <div className="form-row">
        <label htmlFor="stf-source">Source</label>
        <span>
          <select id="stf-source" value={source} onChange={(e) => pickSource(e.target.value as Source)}>
            <option value="stock">Stock</option><option value="uploaded">Uploaded</option>
          </select>
          <button type="button" disabled={upload === null} onClick={() => setBrowsing(true)}>Upload…</button>
        </span>
      </div>
      <div className="form-row">
        <label htmlFor="stf-profile">Profile</label>
        <span>
          <select id="stf-profile" value={form.profile} onChange={(e) => onChange({ profile: e.target.value })} disabled={files === null}>
            <option value="">{files === null ? 'Loading…' : '— choose a profile —'}</option>
            {options?.map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
          {listError && <span role="alert" className="error"> {listError}</span>}
        </span>
      </div>
      {form.profile && (
        <div className="form-row full" aria-label="Profile details">
          <span className="row-label">Profile details</span>
          <div className="stf-profile-info">
            {!shown && !shownError && <span className="muted">Reading the profile…</span>}
            {shownError && <span role="alert" className="error">{shownError}</span>}
            {shown && (
              <>
                <div>Templates: {shown.templates.length ? shown.templates.join(', ') : 'none'}</div>
                <div>YAML duration: {typeof shown.duration === 'number' ? `${shown.duration} s` : 'not set'}</div>
                {shown.warnings.length > 0 && <ul aria-label="Profile warnings" className="muted">{shown.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
                {shown.errors.length > 0 && <ul aria-label="Profile errors" className="error">{shown.errors.map((w, i) => <li key={i}>{w}</li>)}</ul>}
              </>
            )}
          </div>
        </div>
      )}
      {field('stf-d', 'Duration -d (s)', 'd', form.d, problems.d, { min: STF_D_MIN, max: STF_D_MAX })}
      {field('stf-m', 'Multiplier -m', 'm', form.m, problems.m, { min: 0, max: STF_M_MAX, step: 'any' })}
      {field('stf-l', 'Latency -l (pps, 0 = off)', 'l', form.l, problems.l, { min: 0, max: STF_L_MAX })}
      {field('stf-rx', 'Rx-check --rx-check (0 = off)', 'rxCheck', form.rxCheck, problems.rxCheck, { min: 0, max: STF_RX_CHECK_MAX })}
      {field('stf-monitor-port', 'Monitor port', 'monitorPort', monitorPort, problems.monitorPort, { min: 1, max: 65535 },
        <Info label="the Monitor port">the PUB port {host ?? HOST_FALLBACK} reaches the run's publisher on</Info>)}
        </div>
      {browsing && <BrowseDialog title="Pick a stateful YAML" onClose={() => setBrowsing(false)} onPick={(p) => { setBrowsing(false); setUploadPath(p) }} />}
      {uploadPath !== null && upload && <StfUploadDialog path={uploadPath} target={upload} onUploaded={uploaded} onClose={() => setUploadPath(null)} />}
    </>
  )
}
