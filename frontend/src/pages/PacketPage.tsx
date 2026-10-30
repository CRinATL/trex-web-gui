import { useCallback, useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { toStream, type PacketDoc } from '../api'
import { ConfirmDialog } from '../components/daemon/ConfirmDialog'
import { AddToProfileDialog } from '../components/packet/AddToProfileDialog'
import { PacketEditor } from '../components/packet/PacketEditor'
import { PcapPicker } from '../components/packet/PcapPicker'
import { PresetsDialog } from '../components/packet/PresetsDialog'
import { ModalLayer } from '../components/packet/ModalLayer'
import { Templates } from '../components/packet/Templates'
import { newDoc, readTrp, writeTrp } from '../components/packet/trpFile'
import { downloadBlob } from '../download'
import { useHotkey } from '../keys'
import { toPcap } from '../pcapWriter'

const TRP_MAX = 4 * 1024 * 1024    // the backend's PACKET_BODY_MAX: a bigger file could not be built
const OPEN_NOTE = 'A .trp may contain expressions; they are evaluated on the backend host when the packet is built.'
const DROPPED = 'The packet changed while the other one was being opened: the opened one was dropped.'

const errText = (x: unknown) => (x instanceof Error ? x.message : String(x))
// A file name from a document name: `/` is a directory in a template name, and not a file name character.
const fileBase = (name: string | null) => (name ?? '').replace(/\.trp$/i, '').replace(/[\\/]/g, '-').trim() || 'packet'

type Notice = { kind: 'status' | 'error'; text: string }
type DialogKind = 'templates' | 'presets' | 'pcap' | 'add'

function FileMenu({ items, active }: { items: { label: string; note?: string; run: () => void }[]; active: boolean }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const noteId = useId()
  if (open && !active) setOpen(false)   // a hidden page closes its menu
  useHotkey('escape', () => setOpen(false), open && active)
  // a press outside the whole menu (button included, so the button's own click can toggle) closes it
  useEffect(() => {
    if (!open) return
    const down = (e: MouseEvent) => { const root = ref.current?.parentElement; if (root && !root.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', down)
    return () => document.removeEventListener('mousedown', down)
  }, [open])
  return (
    <span className="setups-menu">
      <button type="button" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>File ▾</button>
      {open && (
        <div role="menu" aria-label="File" className="setups-menu-list" ref={ref}>
          {items.map((it, i) => (
            <div key={it.label}>
              <button type="button" role="menuitem" {...(it.note ? { 'aria-describedby': `${noteId}-${i}` } : {})} onClick={() => { setOpen(false); it.run() }}>{it.label}</button>
              {it.note && <span id={`${noteId}-${i}`} className="muted menu-note">{it.note}</span>}
            </div>
          ))}
        </div>
      )}
    </span>
  )
}

// The standalone packet editor (D5): the packet as a .trp file or a template, opened from a pcap or a preset, saved as a
// download, and added to a library profile as a new stream. The page stays mounted while hidden (App.tsx), so the packet,
// its undo history and its name survive a visit to another page. `active` false: no keys, no menu.
export function PacketPage({ active = true }: { active?: boolean }) {
  const [doc, setDoc] = useState<PacketDoc>(newDoc)
  const [name, setName] = useState<string | null>(null)
  const [saved, setSaved] = useState(() => writeTrp(doc))      // the document as last opened or saved: the title's • is a difference from it
  const [flags, setFlags] = useState<number | null>(null)      // a preset's MAC-mode bits, which the stream made from this packet carries
  const [dialog, setDialog] = useState<{ kind: DialogKind; origin: PacketDoc } | null>(null)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [discard, setDiscard] = useState<{ run: () => void } | null>(null)   // a replacement waiting for the user to agree to lose the unsaved changes
  const docRef = useRef(doc)
  const savedRef = useRef(saved)
  const fileInput = useRef<HTMLInputElement>(null)
  useEffect(() => { docRef.current = doc }, [doc])
  useEffect(() => { savedRef.current = saved }, [saved])
  const dirty = writeTrp(doc) !== saved

  // Puts a document into the editor (which clears its history). `origin` is the document the request that produced `next`
  // started from: if the editor holds another one now, `next` is stale and dropped (checked again when the user agrees to
  // discard). Unsaved changes are asked about in the app's own confirm dialog; Cancel leaves everything as it was.
  function adopt(next: PacketDoc, nm: string | null, fl: number | null, origin: PacketDoc, warnings: string[] = []): void {
    const apply = () => {
      if (docRef.current !== origin) { setNotice({ kind: 'error', text: DROPPED }); setDialog(null); return }
      setDoc(next); setName(nm); setFlags(fl); setSaved(writeTrp(next)); setDialog(null)
      setNotice(warnings.length > 0 ? { kind: 'status', text: warnings.join(' ') } : null)
    }
    if (docRef.current !== origin) { apply(); return }
    if (writeTrp(docRef.current) !== savedRef.current) setDiscard({ run: apply })
    else apply()
  }
  const show = (kind: DialogKind) => setDialog({ kind, origin: docRef.current })
  const close = useCallback(() => setDialog(null), [])

  const newPacket = () => adopt(newDoc(), null, null, docRef.current)
  const openFile = () => fileInput.current?.click()
  async function onFile(file: File | undefined) {
    if (!file) return
    const origin = docRef.current
    try {
      if (file.size > TRP_MAX) throw new Error(`larger than ${TRP_MAX / 1024 / 1024} MiB, the most the backend accepts`)
      adopt(readTrp(await file.text()), fileBase(file.name), null, origin)
    } catch (x) { setNotice({ kind: 'error', text: `${file.name}: ${errText(x)}` }) }
  }
  function save() {
    const d = docRef.current
    downloadBlob(`${fileBase(name)}.trp`, new Blob([writeTrp(d)], { type: 'application/json' }))
    setSaved(writeTrp(d))
  }
  // One packet in a pcap file (D4), written in the browser from the bytes the backend builds.
  async function savePcap() {
    try {
      const { packet } = await toStream(docRef.current)
      downloadBlob(`${fileBase(name)}.pcap`, toPcap([{ binary: packet.binary, ts_rel_us: 0, wirelen: atob(packet.binary).length }]))
      setNotice(null)
    } catch (x) { setNotice({ kind: 'error', text: `Save as pcap: ${errText(x)}` }) }
  }

  function onKeyDown(e: KeyboardEvent<HTMLElement>) {
    if (!active || e.defaultPrevented || !(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey) return
    const k = e.key.toLowerCase()
    if (k === 'o') { e.preventDefault(); openFile() } else if (k === 's') { e.preventDefault(); save() }
  }

  return (
    <section className="packet-page" onKeyDown={onKeyDown}>
      <h1>Packet editor — {name ?? 'Untitled'}{dirty && <> <span title="Unsaved changes" aria-label="unsaved changes">•</span></>}</h1>
      <div className="setups-buttons">
        <FileMenu active={active} items={[
          { label: 'New', run: newPacket },
          { label: 'Open .trp…', note: OPEN_NOTE, run: openFile },
          { label: 'Save', note: 'Downloads a .trp file.', run: save },
          { label: 'Save as template…', run: () => show('templates') },
          { label: 'Open pcap…', run: () => show('pcap') },
          { label: 'Save as pcap', run: () => void savePcap() },
          { label: 'Add to profile as a new stream…', run: () => show('add') },
        ]} />
        <button type="button" onClick={() => show('templates')}>Templates…</button>
        <button type="button" onClick={() => show('presets')}>Presets…</button>
        <input ref={fileInput} type="file" accept=".trp,.json,application/json" aria-label="Packet file" hidden
          onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ''; void onFile(f) }} />
      </div>
      {notice && <p role={notice.kind === 'error' ? 'alert' : 'status'} className={notice.kind === 'error' ? 'error' : 'muted'}>{notice.text}</p>}
      <PacketEditor doc={doc} onDocChange={setDoc} mode="standalone" onOpen={openFile} onSave={save} />
      {dialog && (
        <ModalLayer>
          {dialog.kind === 'templates' && <Templates doc={doc} active={active && !discard} onClose={close} onOpen={(d, n) => adopt(d, n, null, dialog.origin)} />}
          {dialog.kind === 'presets' && <PresetsDialog active={active && !discard} onClose={close} onOpen={(d, f) => adopt(d, null, f, dialog.origin)} />}
          {dialog.kind === 'pcap' && <PcapPicker active={active && !discard} onClose={close} onPick={(d, w) => adopt(d, null, null, dialog.origin, w)} />}
          {dialog.kind === 'add' && <AddToProfileDialog doc={doc} flags={flags} active={active && !discard} onClose={close} />}
        </ModalLayer>
      )}
      {discard && (
        <ModalLayer>
          <ConfirmDialog title="Discard changes?" confirmLabel="Discard" danger onClose={() => setDiscard(null)}
            body={<p>The packet has unsaved changes. Replacing it discards them.</p>}
            onConfirm={async () => { const { run } = discard; setDiscard(null); run() }} />
        </ModalLayer>
      )}
    </section>
  )
}
