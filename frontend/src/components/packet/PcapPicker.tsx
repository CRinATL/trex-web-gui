import { useRef, useState } from 'react'
import { ApiError, pcapList, pcapPacket, uploadFile, type PacketDoc, type PcapPage, type PcapSource } from '../../api'
import { useHotkey } from '../../keys'
import { BrowseDialog } from '../BrowseDialog'
import { useDialogFocus } from '../useDialogFocus'

export const PAGE_ROWS = 100       // the backend's PCAP_PAGE_MAX
const CAP_SETTING = 'pcap_size_cap_mib'

const errText = (x: unknown) => {
  const m = x instanceof Error ? x.message : String(x)
  // a capture over the cap is refused before it is read: say where the cap is set
  return x instanceof ApiError && x.status === 413 && !m.includes(CAP_SETTING)
    ? `${m}. The size cap is the ${CAP_SETTING} setting (Settings, Files on the backend host).` : m
}

// Picks one packet from a capture (D4): on the backend host or from this computer, 100 packets a page. `onPick` gets the
// packet's document and the warnings that came with it (a cut capture, truncated records, a value kept as a literal).
export function PcapPicker({ onPick, onClose, active = true }: {
  onPick: (doc: PacketDoc, warnings: string[]) => void; onClose: () => void; active?: boolean
}) {
  const ref = useDialogFocus<HTMLDivElement>('[data-autofocus]')
  const [from, setFrom] = useState<'host' | 'upload'>('host')
  const [path, setPath] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [browsing, setBrowsing] = useState(false)
  const [page, setPage] = useState<{ offset: number; data: PcapPage } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const src = useRef<PcapSource | null>(null)                          // where the shown page came from
  const uploaded = useRef<{ file: File; id: string } | null>(null)     // the upload of the chosen file, kept for the next page
  const seq = useRef(0)                                                 // a reply to an older request is dropped
  useHotkey('escape', () => { if (active && !browsing) onClose() })

  async function source(): Promise<PcapSource | null> {
    if (from === 'host') return path.trim() === '' ? null : { path: path.trim() }
    if (!file) return null
    if (uploaded.current?.file !== file) uploaded.current = { file, id: (await uploadFile(file)).upload_id }
    return { upload_id: uploaded.current.id }
  }
  // Reads the page at `offset` of the chosen capture (a new source starts at 0).
  async function load(offset: number, fresh: boolean) {
    const mine = ++seq.current
    setBusy(true); setError(null)
    try {
      const s = fresh ? await source() : src.current
      if (s === null) { setError(from === 'host' ? 'Give the path of a capture on the backend host.' : 'Choose a capture file.'); return }
      const data = await pcapList(s, offset)
      if (mine !== seq.current) return
      src.current = s
      setPage({ offset, data })
    } catch (x) {
      if (mine !== seq.current) return
      if (x instanceof ApiError && x.status === 404) uploaded.current = null    // an expired upload is sent again on the next try
      setError(errText(x)); setPage(null)
    } finally { if (mine === seq.current) setBusy(false) }
  }
  async function pick(index: number) {
    const s = src.current
    if (s === null) return
    const mine = ++seq.current
    setBusy(true); setError(null)
    try {
      const r = await pcapPacket(s, index)
      if (mine === seq.current) onPick(r.doc, r.warnings)
    } catch (x) { if (mine === seq.current) setError(errText(x)) } finally { if (mine === seq.current) setBusy(false) }
  }

  const rows = page?.data.rows ?? []
  const first = page ? page.offset + 1 : 0
  const last = page ? page.offset + rows.length : 0
  return (
    <div role="dialog" aria-modal="true" aria-label="Open pcap" className="modal packet-dialog pcap-picker" ref={ref}>
      <h2>Open a packet from a pcap</h2>
      <form onSubmit={(e) => { e.preventDefault(); void load(0, true) }}>
        <fieldset className="plain">
          <legend>Capture file</legend>
          <label><input type="radio" name="pcap-from" checked={from === 'host'} onChange={() => setFrom('host')} /> On the backend host</label>{' '}
          <label><input type="radio" name="pcap-from" checked={from === 'upload'} onChange={() => setFrom('upload')} /> From this computer</label>
          {from === 'host'
            ? <p><input type="text" aria-label="Capture path" value={path} spellCheck={false} placeholder="/path/to/capture.pcap" onChange={(e) => setPath(e.target.value)} />{' '}
                <button type="button" onClick={() => setBrowsing(true)}>Browse…</button></p>
            : <p><input type="file" aria-label="Capture file" accept=".pcap,.cap" onChange={(e) => { setFile(e.target.files?.[0] ?? null); uploaded.current = null }} /></p>}
          <button type="submit" disabled={busy}>List packets</button>
        </fieldset>
      </form>
      {error && <p role="alert" className="error">{error}</p>}
      {page && (
        <>
          {page.data.warnings.length > 0 && (
            <ul className="muted" role="status" aria-label="Capture warnings">{page.data.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
          )}
          <p className="pcap-range">{page.data.total === 0 ? 'No packets' : `Packets ${first}–${last} of ${page.data.total}`}{' '}
            <button type="button" disabled={busy || page.offset === 0} onClick={() => void load(Math.max(0, page.offset - PAGE_ROWS), false)}>Previous</button>{' '}
            <button type="button" disabled={busy || page.offset + rows.length >= page.data.total} onClick={() => void load(page.offset + PAGE_ROWS, false)}>Next</button></p>
          <div className="pcap-rows">
            <table>
              <thead><tr><th scope="col">#</th><th scope="col">Bytes</th><th scope="col">Summary</th></tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.index}>
                    <td><button type="button" disabled={busy} aria-label={`Open packet ${r.index + 1}`} onClick={() => void pick(r.index)}>{r.index + 1}</button></td>
                    <td>{r.length}</td><td className="mono">{r.summary}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      <div className="dialog-footer"><div className="end"><button type="button" data-autofocus onClick={onClose}>Cancel</button></div></div>
      {browsing && <BrowseDialog title="Browse for a capture" pcap active={active} onClose={() => setBrowsing(false)} onPick={(p) => { setBrowsing(false); setPath(p) }} />}
    </div>
  )
}
