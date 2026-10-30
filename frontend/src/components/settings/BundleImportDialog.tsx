import { useState } from 'react'
import { ApiError, type Bundle, type BundleMode, type BundlePreview } from '../../api'
import { useHotkey } from '../../keys'
import { useDialogFocus } from '../useDialogFocus'

// The sections the backend writes, in the order they are listed. `theme` is the browser's own and has a checkbox instead of a mode.
const SECTIONS = ['preferences', 'connections', 'port_labels', 'test_setups'] as const
const LABELS: Record<string, string> = { preferences: 'Preferences', connections: 'Connections', port_labels: 'Port labels', test_setups: 'Test setups' }

// Preferences hold paths that run later (Wireshark, a config file), so they are skipped unless chosen, and have no merge.
const defaultMode = (name: string): BundleMode => (name === 'preferences' ? 'skip' : 'merge')
const modesFor = (name: string): BundleMode[] => (name === 'preferences' ? ['replace', 'skip'] : ['replace', 'merge', 'skip'])

// What each mode does to an item that the file adds, changes or lacks (removes: here, not in the file).
const VERBS: Record<BundleMode, { add: string; change: string; remove: string }> = {
  merge: { add: 'added', change: 'overwritten', remove: 'kept (on this GUI, not in the file; replace would delete it)' },
  replace: { add: 'added', change: 'overwritten', remove: 'deleted' },
  skip: { add: 'not added', change: 'not overwritten', remove: 'kept' },
}

// Header fields come from the file and the backend does not check their type: show a string (cut short) or a number, else nothing.
const shown = (v: unknown): string | null => (typeof v === 'string' ? (v.length > 60 ? `${v.slice(0, 60)}…` : v) : typeof v === 'number' ? String(v) : null)

// What an import would do, section by section, with the mode of each. `onApply` gets the chosen modes, plus `theme` ('replace' when
// "also apply theme" is ticked, else 'skip'); the caller strips `theme` before it talks to the backend, because the theme is per browser.
export function BundleImportDialog({ bundle, preview, onApply, onClose }: {
  bundle: Bundle; preview: BundlePreview; onApply: (modes: Record<string, BundleMode>) => Promise<void>; onClose: () => void
}) {
  const ref = useDialogFocus<HTMLDivElement>('[data-autofocus]')
  const present = SECTIONS.filter((n) => preview.sections[n]?.present)
  const [modes, setModes] = useState<Record<string, BundleMode>>(() => Object.fromEntries(present.map((n) => [n, defaultMode(n)])))
  const theme = preview.sections.theme
  const themeOffered = !!theme?.present && theme.valid
  const [withTheme, setWithTheme] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | Error | null>(null)
  useHotkey('escape', () => { if (!busy) onClose() })   // an Apply in flight must report its result here
  const blocked = present.filter((n) => modes[n] !== 'skip' && !preview.sections[n].valid)
  const nothing = present.every((n) => modes[n] === 'skip') && !(themeOffered && withTheme)

  async function apply() {
    setBusy(true); setError(null)
    try { await onApply({ ...modes, theme: themeOffered && withTheme ? 'replace' : 'skip' }) }
    catch (x) { setError(x instanceof Error ? x : new Error(String(x))) }
    finally { setBusy(false) }
  }
  const written = error instanceof ApiError && Array.isArray(error.body.written) ? (error.body.written as string[]) : null
  const failed = error instanceof ApiError && typeof error.body.failed === 'string' ? error.body.failed : null

  // The warning only applies where a section holds something here that a replace would delete.
  const keptAtRisk = present.some((n) => modes[n] !== 'replace' && preview.sections[n].removes.length > 0)

  return (
    <div className="modal bundle-import" role="dialog" aria-modal="true" aria-labelledby="bundle-title" ref={ref}>
      <h2 id="bundle-title">Import settings</h2>
      <p className="muted">Exported {shown(bundle.exported_at) ?? 'at an unknown time'} by trex-web-gui {shown(bundle.app_version) ?? '?'}. Nothing is written until you press Apply, and an import runs nothing by itself.</p>
      <div className="bundle-body">
        {present.length === 0 && <p role="status">The file has none of the sections this page can import.</p>}
        {present.length > 0 && <table>
          <thead><tr><th scope="col">Section</th><th scope="col">Mode</th><th scope="col">What happens</th></tr></thead>
          <tbody>
            {present.map((n, i) => {
              const sec = preview.sections[n]
              const mode = modes[n]
              const verbs = VERBS[mode]
              const setups = n === 'test_setups'
              // the backend's flag already reads "`key` changes from … to …"; replace adds nothing, any other mode says the change is not made
              const flagPrefix = n === 'preferences' && mode !== 'replace' ? 'skipped:' : null
              const changed = sec.adds.length + sec.changes.length + sec.removes.length > 0
              // a section that failed to parse has empty lists too, so it is only "the same" when the backend could read it
              const idle = sec.valid && !changed && !(n === 'preferences' && sec.flags.length > 0)
              const flags = sec.flags.length > 0 && <ul aria-label={`${LABELS[n]} flags`}>{sec.flags.map((f, j) => <li key={`${j}${f}`}>{flagPrefix && <span className="muted">{flagPrefix} </span>}{f}</li>)}</ul>
              return (
                <tr key={n}>
                  <th scope="row">{LABELS[n]}</th>
                  <td>
                    <select aria-label={`${LABELS[n]} mode`} value={mode} data-autofocus={i === 0 ? true : undefined}
                      onChange={(e) => setModes({ ...modes, [n]: e.target.value as BundleMode })}>
                      {modesFor(n).map((m) => <option key={m} value={m}>{m}</option>)}
                    </select>
                  </td>
                  <td>
                    {idle && <p className="muted">same as here, nothing to do</p>}
                    {changed && <ul aria-label={`${LABELS[n]} items`}>
                      {sec.adds.map((x) => <li key={`a${x}`}>{x}: {verbs.add}</li>)}
                      {sec.changes.map((x) => <li key={`c${x}`}>{x}: {verbs.change}</li>)}
                      {sec.removes.map((x) => <li key={`r${x}`}>{x}: {verbs.remove}</li>)}
                    </ul>}
                    {sec.errors.length > 0 && <ul className="error" role="alert">{sec.errors.map((e, j) => <li key={`${j}${e}`}>{e}</li>)}</ul>}
                    {!setups && flags}
                    {setups && (sec.flags.length > 0 || preview.profile_paths.length > 0 || preview.path_changes.length > 0) && (
                      <details className="fold">
                        <summary>Before you Load these ({preview.profile_paths.length} profiles, {preview.path_changes.length} re-pointed)</summary>
                        {flags}
                        {preview.profile_paths.length > 0 && <>
                          <p>Profile files these setups run from (a Load inspects them, which runs a .py file on the backend host):</p>
                          <ul aria-label="Profile paths">{preview.profile_paths.map((p) => <li key={p}><code>{p}</code></li>)}</ul>
                        </>}
                        {preview.path_changes.length > 0 && <>
                          <p>Setups here that would point at another file:</p>
                          <ul aria-label="Re-pointed setups">
                            {preview.path_changes.map((c) => <li key={`${c.slug}/${c.port}`}>{c.slug}, port {c.port}: <code>{c.from}</code> becomes <code>{c.to}</code></li>)}
                          </ul>
                        </>}
                      </details>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>}
        {theme?.present && (themeOffered
          ? <label className="check"><input type="checkbox" checked={withTheme} onChange={(e) => setWithTheme(e.target.checked)} />Also apply theme</label>
          : <ul className="error" role="alert">{theme.errors.map((e, i) => <li key={`${i}${e}`}>{e}</li>)}</ul>)}
        {preview.ignored.length > 0 && <p className="muted">Ignored sections: {preview.ignored.join(', ')}</p>}
      </div>
      {blocked.length > 0 && <p role="status" className="error">Set {blocked.map((n) => LABELS[n]).join(', ')} to skip, or fix the file: a section with errors cannot be applied.</p>}
      {error && <div role="alert" className="error">
        <p>{error.message}</p>
        {written && <p>Already written: {written.length > 0 ? written.join(', ') : 'nothing'}</p>}
        {failed && <p>Failed: {failed}</p>}
      </div>}
      <div className="dialog-footer">
        {keptAtRisk && <span className="muted">Switching to replace deletes what is kept</span>}
        <div className="end">
          <button type="button" disabled={busy} onClick={onClose}>Cancel</button>
          <button type="button" disabled={busy || blocked.length > 0 || nothing} onClick={() => void apply()}>Apply</button>
        </div>
      </div>
    </div>
  )
}
