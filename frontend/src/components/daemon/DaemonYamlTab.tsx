import { useEffect, useState } from 'react'
import { daemonConfigPush, type ConfigError } from '../../api'
import { ConfirmDialog } from './ConfirmDialog'
import type { DaemonTarget, PushedConfig } from './daemonForm'
import type { Values } from './configModel'
import type { PreviewState } from './useConfigPreview'

const FILENAME_RE = /^[A-Za-z0-9_.-]{1,64}\.ya?ml$/   // the backend's rule; it also refuses a name starting with tg-
const DEFAULT_NAME = 'trex-web-gui-<user>.yaml'

// "line 3: message" for a text error with a line, else "path: message".
const errorLine = (e: ConfigError) => (e.line !== null ? `line ${e.line}: ${e.message}` : e.path ? `${e.path}: ${e.message}` : e.message)

// What the Push confirmation shows and sends: the YAML as previewed, the name asked for (null: the backend's default), and the
// daemon and directory it names, all as they were when Push… was pressed.
interface PushSnap { yaml: string; filename: string | null; values: Values | null; where: string }

// The YAML tab: the rendered config with its problems, an Edit YAML switch, and Push, which asks first. `editing` and `text` belong
// to the dialog (the Config tab reads the same preview). Push is disabled until the preview answers for exactly what is on screen.
export function DaemonYamlTab({ preview, editing, text, canEdit, connected, target, filesPath, onEditing, onText, onPushed, onModal }: {
  preview: PreviewState; editing: boolean; text: string; canEdit: boolean; connected: boolean; target: DaemonTarget | null; filesPath: string | null
  onEditing: (on: boolean) => void; onText: (t: string) => void; onPushed: (p: PushedConfig) => void; onModal: (open: boolean) => void
}) {
  const [filename, setFilename] = useState('')
  const [snap, setSnap] = useState<PushSnap | null>(null)
  useEffect(() => { onModal(snap !== null) }, [snap, onModal])
  useEffect(() => () => onModal(false), [onModal])

  const { result, failure, ready } = preview
  const errors = failure === null && result ? result.errors : []
  const nameBad = filename !== '' && (!FILENAME_RE.test(filename) || filename.startsWith('tg-'))
  const pushOk = connected && ready && failure === null && result !== null && result.errors.length === 0 && !nameBad
  // Back to the form needs a config that parsed and passed.
  const canLeave = ready && failure === null && result !== null && result.errors.length === 0 && result.values !== null

  async function push() {
    if (!snap) return
    const r = await daemonConfigPush({ yaml: snap.yaml, filename: snap.filename, confirm: true })
    const num = (k: string) => { const v = snap.values?.[k]; return typeof v === 'number' ? v : undefined }
    setSnap(null)
    onPushed({ path: r.path, values: { zmq_rpc_port: num('zmq_rpc_port'), zmq_pub_port: num('zmq_pub_port') } })
  }

  return (
    <div className="daemon-yaml">
      <div className="action-bar">
        <label><input type="checkbox" checked={editing} disabled={editing ? !canLeave : !(canEdit && ready && failure === null && result !== null)} onChange={(e) => onEditing(e.target.checked)} /> Edit YAML</label>
        <label htmlFor="daemon-yaml-file">Filename</label>
        <input id="daemon-yaml-file" value={filename} placeholder={DEFAULT_NAME} aria-invalid={nameBad || undefined} onChange={(e) => setFilename(e.target.value)} />
        <button type="button" disabled={!pushOk} onClick={() => result && setSnap({ yaml: result.yaml, filename: filename || null, values: result.values, where: `${filesPath ?? 'the daemon\'s files directory'} on ${target ? `${target.host}:${target.port}` : 'the daemon'}` })}>Push…</button>
      </div>
      {nameBad && <p className="error" role="alert">The name must be letters, digits, _ . -, end in .yaml or .yml, and not start with tg-</p>}
      {failure && <p className="error" role="alert">{failure}</p>}
      {errors.length > 0 && <ul className="error" aria-label="Config problems">{errors.map((e, i) => <li key={i}><span aria-hidden="true">⚠ </span>{editing ? errorLine(e) : e.path ? `${e.path}: ${e.message}` : e.message}</li>)}</ul>}
      {editing
        ? <textarea className="daemon-yaml-text" aria-label="YAML text" spellCheck={false} value={text} onChange={(e) => onText(e.target.value)} />
        : <pre className="daemon-yaml-view" aria-label="YAML preview">{result?.yaml ?? (canEdit ? '' : 'Connect to a daemon to build a config.')}</pre>}
      {snap && (
        <ConfirmDialog title="Push config" confirmLabel="Push" onConfirm={push} onClose={() => setSnap(null)}
          body={<><p>{`Write ${snap.filename ?? `${DEFAULT_NAME} (named for the backend's user)`} to ${snap.where}?`}</p><pre><code>{snap.yaml}</code></pre></>} />
      )}
    </div>
  )
}
