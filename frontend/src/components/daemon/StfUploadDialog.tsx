import { useEffect, useState } from 'react'
import { ApiError, daemonStfUpload, daemonStfUploadPreview, type UploadPreview } from '../../api'
import { useHotkey } from '../../keys'
import { useDialogFocus } from '../useDialogFocus'
import { ConfirmDialog } from './ConfirmDialog'
import { formatBytes } from './daemonForm'

const errText = (x: unknown) => (x instanceof ApiError || x instanceof Error ? x.message : String(x))

// Where an upload goes: the daemon as host:port and its files directory.
export interface UploadTarget { daemon: string; filesPath: string }

// What the confirmation shows and sends, taken when Push… is pressed.
interface Snap { files: number; bytes: number; filesPath: string; daemon: string; yamlRemote: string }

// The upload dialog (spec 13.7): reads the local profile and its pcaps on the backend's host (a preview that writes nothing),
// lists every file with its pushed name, and pushes them after a confirmation. `onUploaded` gets the new profile's path on the
// daemon (also when everything was already there, so nothing is written).
export function StfUploadDialog({ path, target, onUploaded, onClose }: {
  path: string; target: UploadTarget; onUploaded: (profile: string) => void; onClose: () => void
}) {
  const ref = useDialogFocus<HTMLDivElement>('[data-autofocus]')
  const [preview, setPreview] = useState<UploadPreview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [snap, setSnap] = useState<Snap | null>(null)
  useHotkey('escape', onClose, snap === null)

  useEffect(() => {
    let live = true
    daemonStfUploadPreview(path).then((r) => { if (live) setPreview(r) }, (x) => { if (live) setError(errText(x)) })
    return () => { live = false }
  }, [path])

  // Only what is not on the server yet is written (the backend skips the rest).
  const writes = preview ? preview.files.filter((f) => !f.exists) : []
  const writeBytes = writes.reduce((n, f) => n + f.bytes, 0) + (preview && !preview.yaml_exists ? preview.yaml_bytes : 0)
  const writeCount = writes.length + (preview && !preview.yaml_exists ? 1 : 0)
  const yesNo = (b: boolean) => (b ? 'yes' : 'no')

  // Sends the preview's profile name and the daemon the confirm named, from the snapshot: if the files changed since, or another
  // daemon is connected now, the backend refuses (plan_changed).
  async function push() {
    if (!snap) return
    const r = await daemonStfUpload({ path, confirm: true, expected_yaml_remote: snap.yamlRemote, expected_target: snap.daemon })
    setSnap(null)
    onUploaded(r.profile)
  }

  return (
    <div className="modal stf-upload-dialog" role="dialog" aria-modal="true" aria-label="Upload a stateful profile" ref={ref}>
      <h2>Upload a stateful profile</h2>
      <p className="path">{path}</p>
      {error && <p role="alert" className="error">{error}</p>}
      {!preview && !error && <p className="muted">Reading the profile and its pcaps…</p>}
      {preview && (
        <>
          <table aria-label="Files to upload">
            <thead><tr><th>Template</th><th>Local file</th><th>Size</th><th>Remote name</th><th>Already on the server</th></tr></thead>
            <tbody>
              {preview.files.map((f) => (
                <tr key={f.remote}>
                  <td>{f.template}</td><td>{f.local}</td><td>{formatBytes(f.bytes)}</td><td>{f.remote}</td><td>{yesNo(f.exists)}</td>
                </tr>
              ))}
              <tr>
                <td>(the profile)</td><td>{path}</td><td>{formatBytes(preview.yaml_bytes)}</td><td>{preview.yaml_remote}</td><td>{yesNo(preview.yaml_exists)}</td>
              </tr>
            </tbody>
          </table>
          <p>Total: {preview.files.length + 1} files, {formatBytes(preview.total_bytes)}</p>
          {preview.warnings.length > 0 && <ul aria-label="Upload warnings" className="muted">{preview.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
          {writeCount === 0 && <p role="status" className="muted">Everything is already on the server.</p>}
        </>
      )}
      <div className="dialog-footer">
        <div className="end">
          <button type="button" data-autofocus onClick={onClose}>Cancel</button>
          {preview && writeCount === 0
            ? <button type="button" onClick={() => onUploaded(preview.profile)}>Use it</button>
            : <button type="button" disabled={!preview} onClick={() => preview && setSnap({ files: writeCount, bytes: writeBytes, filesPath: target.filesPath, daemon: target.daemon, yamlRemote: preview.yaml_remote })}>Push…</button>}
        </div>
      </div>
      {snap && (
        <ConfirmDialog title="Push files" confirmLabel="Push" onConfirm={push} onClose={() => setSnap(null)}
          body={<p>{`Write ${snap.files} ${snap.files === 1 ? 'file' : 'files'} (${formatBytes(snap.bytes)}) to ${snap.filesPath} on ${snap.daemon}? The daemon cannot delete them; they stay until the host clears ${snap.filesPath}.`}</p>} />
      )}
    </div>
  )
}
