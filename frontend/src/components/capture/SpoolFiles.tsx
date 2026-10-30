import { useState } from 'react'
import { captureFileUrl, deleteCaptureFile } from '../../api'
import { errText } from '../../portGate'
import type { SpoolSource } from './useSpoolListing'

// The pcap files the backend has spooled: where they are, what each holds, Download and Delete.
export function SpoolFiles({ spool }: { spool: SpoolSource }) {
  const { listing, reload } = spool
  const [actionError, setActionError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState<string | null>(null)
  const error = actionError ?? spool.error

  async function remove(name: string) {
    if (confirming !== name) { setConfirming(name); return }
    setConfirming(null)
    try { await deleteCaptureFile(name); setActionError(null) }
    catch (x) { setActionError(errText(x)) }
    reload()
  }

  return (
    <section className="spool-files" aria-label="Saved files">
      <h2>Saved files</h2>
      {error && <p role="alert" className="error">{error}</p>}
      {listing && (
        <>
          <p className="muted">Saved to {listing.dir} on the backend host</p>
          <p>{listing.total} {listing.total === 1 ? 'file' : 'files'}</p>
          <table>
            <thead><tr><th>Name</th><th>Size</th><th>Saved</th><th>Packets</th><th></th></tr></thead>
            <tbody>
              {listing.files.map((f) => (
                <tr key={f.name}>
                  <td>{f.name} {f.partial && <span className="badge">partial</span>}</td>
                  <td>{f.size} bytes</td>
                  <td>{new Date(f.mtime * 1000).toLocaleString()}</td>
                  <td>{f.pkt_count ?? ''}</td>
                  <td>
                    <a href={captureFileUrl(f.name)} download={f.name}>Download</a>
                    <button type="button" onClick={() => void remove(f.name)} onBlur={() => setConfirming(null)}>
                      {confirming === f.name ? 'Click again to confirm' : 'Delete'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  )
}
