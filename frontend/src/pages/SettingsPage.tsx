import { useEffect, useRef, useState } from 'react'
import { ApiError, applyBundle, exportBundle, getSettings, previewBundle, putSettings, type Bundle, type BundleMode, type BundleResult, type BundlePreview, type Settings, type Preferences } from '../api'
import { BundleImportDialog } from '../components/settings/BundleImportDialog'
import { AboutSection } from '../components/settings/AboutSection'
import { downloadBlob } from '../download'
import { EditedTag, unsavedText } from '../ui/Edited'
import { Info } from '../ui/Info'
import { Tabs, tabPanel } from '../ui/Tabs'
import { useAppState } from '../state'
import { THEMES, readTheme, useTheme, type Theme } from '../theme'

const sameSettings = (a: Settings | null, b: Settings | null) => JSON.stringify(a) === JSON.stringify(b)
const stamp = () => new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z')
const BUNDLE_LIMIT = 4 * 1024 * 1024   // backend api/bundle.py BODY_LIMIT
const isTheme = (v: unknown): v is Theme => THEMES.some(([t]) => t === v)

type PrefKey = keyof Preferences
type FileKey = 'templates_dir' | 'wireshark_path' | 'workspace_dir' | 'trex_cfg_path' | 'library_dir'
type SectionId = 'appearance' | 'files' | 'session' | 'backup' | 'about'
const SECTIONS: [SectionId, string][] = [['appearance', 'Appearance'], ['files', 'Files on the backend host'], ['session', 'Session'], ['backup', 'Back up and restore'], ['about', 'About']]
// The preferences each saveable section owns.
const SECTION_KEYS: Partial<Record<SectionId, PrefKey[]>> = { files: ['templates_dir', 'workspace_dir', 'library_dir', 'trex_cfg_path', 'wireshark_path', 'pcap_size_cap_mib'], session: ['release_on_exit'] }
// Every preference some section saves.
const PREF_KEYS: PrefKey[] = Object.values(SECTION_KEYS).flat()

// The Theme control: per browser, effective at once, and independent of the backend.
function ThemeFieldset() {
  const { theme, setTheme } = useTheme()
  return (
    <fieldset>
      <legend>Theme</legend>
      {THEMES.map(([value, label]) => (
        <label key={value}><input type="radio" name="theme" checked={theme === value} onChange={() => setTheme(value)} /> {label}</label>
      ))}
    </fieldset>
  )
}

// --monitor-only has no Control session and refuses every write, so only the theme (a per-browser choice) is offered.
export function SettingsPage() {
  const { monitorOnly } = useAppState()
  return monitorOnly ? <section><h1>Settings</h1><ThemeFieldset /></section> : <FullSettingsPage />
}

function FullSettingsPage() {
  const { setTheme } = useTheme()
  const [s, setS] = useState<Settings | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [msg, setMsg] = useState<{ id: SectionId; text: string } | null>(null)   // the result line of the last Save, shown in its section
  const [sel, setSel] = useState<SectionId>('appearance')
  // The last value from the server (GET, PUT or a settings message adopted while nothing was being edited); `s` differs from it while editing.
  const [loaded, setLoaded] = useState<Settings | null>(null)
  const [dataDir, setDataDir] = useState<string | null>(null)
  const live = useAppState().settings
  const [includeTheme, setIncludeTheme] = useState(false)
  const [transfer, setTransfer] = useState<string | null>(null)   // an export or import problem, or the import result line
  const [pending, setPending] = useState<{ bundle: Bundle; preview: BundlePreview } | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  // The newest settings message, and whether one came after mount: the mount-time GET must not overwrite it.
  const liveNow = useRef(live)
  const messageSince = useRef(false)
  useEffect(() => {
    if (live !== liveNow.current) messageSince.current = true
    liveNow.current = live
  }, [live])
  const adopt = (r: { settings: Settings; load_error: string | null; data_dir?: string }) => {
    setS(r.settings); setLoaded(r.settings); setLoadError(r.load_error)
    if (r.data_dir !== undefined) setDataDir(r.data_dir)
  }
  useEffect(() => { getSettings().then((r) => adopt(messageSince.current && liveNow.current ? liveNow.current : r)).catch((e: Error) => setLoadError(e.message)) }, [])
  // A settings message (an import, another tab's Save): taken at once while nothing is being edited, otherwise offered below.
  const [seenLive, setSeenLive] = useState(live)
  if (live !== seenLive) {   // set during render, React's pattern for state that follows a changing input
    setSeenLive(live)
    if (live && loaded && !sameSettings(live.settings, loaded) && sameSettings(s, loaded)) adopt(live)
  }
  const changedOnServer = !!live && !!loaded && !sameSettings(live.settings, loaded) && !sameSettings(live.settings, s) && !sameSettings(s, loaded)
  // A preference whose edit differs from the saved one is marked edited (RC1-31): the control's class, its tag and its description.
  const isEdited = (k: PrefKey) => dirtyKeys([k]).length > 0
  const marks = (k: PrefKey) => (isEdited(k) ? { className: 'edited', 'aria-describedby': `pref-${k}-ed` } : {})
  const tag = (k: PrefKey) => isEdited(k) && <EditedTag id={`pref-${k}-ed`} />
  // A text field of the Files section: the label, the input and, when given, its help behind an Info.
  const field = (cur: Settings, k: FileKey, label: string, help?: string) => (
    <div className="form-row">
      <label htmlFor={`pref-${k}`}>{label}</label>
      <span><input id={`pref-${k}`} {...marks(k)} value={cur.preferences[k] ?? ''} onChange={(e) => setS({ ...cur, preferences: { ...cur.preferences, [k]: e.target.value } })} />
        {tag(k)}{help && <Info label={label}>{help}</Info>}</span>
    </div>
  )
  // A whole-number preference of the Files section (the pcap size cap): a number box, as typed until the backend judges it.
  const numberField = (cur: Settings, k: 'pcap_size_cap_mib', label: string, help: string) => (
    <div className="form-row">
      <label htmlFor={`pref-${k}`}>{label}</label>
      <span><input id={`pref-${k}`} {...marks(k)} type="number" min={1} max={1024} step={1} value={cur.preferences[k] ?? ''}
        onChange={(e) => setS({ ...cur, preferences: { ...cur.preferences, [k]: e.target.value === '' ? 0 : Number(e.target.value) } })} />
        {tag(k)}<Info label={label}>{help}</Info></span>
    </div>
  )
  // The preference keys of `keys` whose edit differs from the last settings adopted from the server.
  const dirtyKeys = (keys: PrefKey[]) => (s && loaded ? keys.filter((k) => s.preferences[k] !== loaded.preferences[k]) : [])
  // Saves one section: the PUT carries the saved settings plus only this section's edits, so an unsaved edit elsewhere is never sent and stays shown as unsaved.
  async function saveSection(id: SectionId, keys: PrefKey[]) {
    if (!s || !loaded) return
    const base = live && !sameSettings(live.settings, loaded) ? live.settings : loaded   // the newest server copy, so an import or another tab's Save is not undone
    const sent = { ...base, preferences: { ...base.preferences, ...Object.fromEntries(dirtyKeys(keys).map((k) => [k, s.preferences[k]])) } } as Settings   // only the edited keys: an unedited one keeps the server's value
    const kept = Object.fromEntries(PREF_KEYS.filter((k) => !keys.includes(k) && s.preferences[k] !== loaded.preferences[k]).map((k) => [k, s.preferences[k]]))
    try {
      const r = await putSettings(sent)
      setLoaded(r.settings); setLoadError(r.load_error)
      if (r.data_dir !== undefined) setDataDir(r.data_dir)
      setS({ ...r.settings, preferences: { ...r.settings.preferences, ...kept } })
      setMsg({ id, text: 'Saved' })
    } catch (x) { setMsg({ id, text: x instanceof ApiError ? x.message : String(x) }) }
  }
  // Puts one section's keys back to the saved values.
  function revertSection(keys: PrefKey[]) {
    if (!s || !loaded) return
    setS({ ...s, preferences: { ...s.preferences, ...Object.fromEntries(keys.map((k) => [k, loaded.preferences[k]])) } })
    setMsg(null)
  }
  // The Save and Revert buttons and the result line closing a section.
  const actions = (id: SectionId, keys: PrefKey[]) => {
    const n = dirtyKeys(keys).length
    const edited = n > 0   // nothing to save or revert until the section has an edit
    return (
    <div className="form-row full">
      <span className="dialog-footer"><button type="button" disabled={!edited} onClick={() => void saveSection(id, keys)}>Save</button>
        <button type="button" disabled={!edited} onClick={() => revertSection(keys)}>Revert</button>
        {edited && <span className="unsaved-count">{unsavedText(n)}</span>}</span>
      {msg?.id === id && <p className="muted">{msg.text}</p>}
    </div>
    )
  }
  async function doExport() {
    setTransfer(null)
    try {
      const b = await exportBundle()
      if (includeTheme) b.sections = { ...b.sections, theme: readTheme() }
      downloadBlob(`trex-web-gui-settings-${stamp()}.json`, new Blob([JSON.stringify(b, null, 2)], { type: 'application/json' }))
    } catch (x) { setTransfer(x instanceof ApiError ? x.message : String(x)) }
  }
  async function pickFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = ''   // the same file can be chosen again
    if (!file) return
    setTransfer(null)
    if (file.size > BUNDLE_LIMIT) { setTransfer(`${file.name} is larger than ${BUNDLE_LIMIT / 1024 / 1024} MiB, the most the backend accepts. Nothing was read.`); return }
    let bundle: Bundle
    try {
      const parsed: unknown = JSON.parse(await file.text())
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) throw new Error('expected a JSON object')
      bundle = parsed as Bundle
    } catch (x) { setTransfer(`${file.name} is not a settings file: ${x instanceof Error ? x.message : String(x)}`); return }
    try { setPending({ bundle, preview: await previewBundle(bundle) }) }
    catch (x) { setTransfer(x instanceof ApiError ? x.message : String(x)) }
  }
  async function doApply(modes: Record<string, BundleMode>) {
    if (!pending) return
    const { theme, ...backend } = modes
    let r: BundleResult
    try { r = await applyBundle(pending.bundle, backend) }
    catch (x) {
      // also shown here, so a partial write is reported even if the dialog is gone; the dialog shows it too and stays open
      if (x instanceof ApiError && Array.isArray(x.body.written)) setTransfer(`Import failed: ${x.message} Written: ${x.body.written.join(', ') || 'nothing'}. Failed: ${String(x.body.failed)}.`)
      else setTransfer(`Import failed: ${x instanceof Error ? x.message : String(x)}`)
      throw x
    }
    const t = pending.bundle.sections.theme
    if (theme === 'replace' && pending.preview.sections.theme?.valid && isTheme(t)) setTheme(t)
    setPending(null)
    setTransfer(`Imported: ${r.written.length > 0 ? r.written.join(', ') : 'nothing'}${theme === 'replace' ? ' (theme applied)' : ''}`)
  }
  const tabs = SECTIONS.map(([id, label]) => ({ id, label: dirtyKeys(SECTION_KEYS[id] ?? []).length > 0 ? `${label} · unsaved` : label }))
  return (
    <section>
      <h1>Settings</h1>
      {loadError && <p role="alert" className="error">{loadError}</p>}
      {s && changedOnServer && <p role="status">Settings changed on the server — <button type="button" onClick={() => { adopt(live); setMsg(null) }}>Reload</button></p>}
      <div className="side-layout">
        <Tabs label="Settings sections" idPrefix="settings" orientation="vertical" tabs={tabs} selected={sel} onSelect={setSel} />
        <div>
          <div {...tabPanel('settings', 'appearance', sel)}><ThemeFieldset /></div>
          <div {...tabPanel('settings', 'files', sel)}>
            {s && (
              <div className="form-grid">
                {field(s, 'templates_dir', 'Templates directory')}
                {field(s, 'workspace_dir', 'Workspace directory')}
                {field(s, 'library_dir', 'Profile library directory', `Empty: <data dir>/profiles${dataDir ? ` (here: ${dataDir}/profiles)` : ''}. Must be an absolute path on the backend host.`)}
                {field(s, 'trex_cfg_path', 'TRex config file (on the backend host)', 'Read at the next connect.')}
                {field(s, 'wireshark_path', 'Wireshark path', 'Runs on the backend host; needs a display there. A pcap download works anywhere.')}
                {numberField(s, 'pcap_size_cap_mib', 'Largest capture the packet editor opens (MiB)', 'The pcap picker refuses a larger file before reading it. 1 to 1024; the default is 64. Uploads from this computer are also limited to 64 MiB.')}
                {actions('files', SECTION_KEYS.files!)}
              </div>
            )}
          </div>
          <div {...tabPanel('settings', 'session', sel)}>
            {s && (
              <div className="form-grid">
                <div className="form-row full">
                  <span><label className="check"><input type="checkbox" {...marks('release_on_exit')} checked={s.preferences.release_on_exit}
                    onChange={(e) => setS({ ...s, preferences: { ...s.preferences, release_on_exit: e.target.checked } })} /> Release my ports when trex-web-gui exits</label>
                    {tag('release_on_exit')}
                    <Info label="Release my ports when trex-web-gui exits">The Disconnect button always releases your ports.</Info></span>
                </div>
                {actions('session', SECTION_KEYS.session!)}
              </div>
            )}
          </div>
          <div {...tabPanel('settings', 'backup', sel)}>
            {s && (
              <fieldset className="plain">
                <legend>Back up and restore</legend>
                <label className="check"><input type="checkbox" checked={includeTheme} onChange={(e) => setIncludeTheme(e.target.checked)} />Include theme</label>
                <button type="button" onClick={() => void doExport()}>Export settings…</button>
                <button type="button" onClick={() => fileInput.current?.click()}>Import settings…</button>
                <input ref={fileInput} type="file" accept="application/json,.json" aria-label="Settings file" hidden onChange={(e) => void pickFile(e)} />
                {transfer && <p role="status">{transfer}</p>}
                <p className="muted">Covers preferences, saved connections, port labels and test setups. Nothing is written until you have seen the preview.</p>
              </fieldset>
            )}
          </div>
          <div {...tabPanel('settings', 'about', sel)}><AboutSection dataDir={dataDir} /></div>
        </div>
      </div>
      {pending && <BundleImportDialog bundle={pending.bundle} preview={pending.preview} onApply={doApply} onClose={() => setPending(null)} />}
    </section>
  )
}
