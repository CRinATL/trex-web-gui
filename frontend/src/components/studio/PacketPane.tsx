import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, exportUrl, getStreams, putStreams, streamSidecar, toStream, type DecodedRow, type PacketDoc, type SidecarEntry, type StreamJson, type StreamPatch, type StreamRow } from '../../api'
import { downloadBlob } from '../../download'
import { useHotkey } from '../../keys'
import { toPcap } from '../../pcapWriter'
import { EditedTag } from '../../ui/Edited'
import { ModalLayer } from '../packet/ModalLayer'
import { PacketEditor } from '../packet/PacketEditor'
import { PacketViewer } from '../packet/PacketViewer'
import { PresetsDialog } from '../packet/PresetsDialog'
import { DiscardEditsDialog } from './DiscardEditsDialog'
import { docForStream, macEdits, saveDiff, same, tooLarge } from './streamPacket'
import { endOf, type ProfileWriter } from './studioWriter'
import { undoPatch, type UndoHistory } from './streamUndo'

const plural = (n: number, what: string) => `${n} ${what}${n === 1 ? '' : 's'}`
const CHANGED = 'The stream changed since the editor was opened (another change moved or replaced it): Cancel, then open it again.'
const DROPPED = 'The packet changed while the other one was being made: the new one was dropped.'

// A refusal's text, with the field a 422 names when the message does not.
function errText(x: unknown): string {
  if (!(x instanceof Error)) return String(x)
  const field = x instanceof ApiError && x.code === 'invalid_stream' ? x.body.field : undefined
  return typeof field === 'string' && !x.message.includes(field) ? `${x.message} (field: ${field})` : x.message
}

type Notice = { kind: 'status' | 'error'; text: string }
// The editor's state: `base` the document it opened with (a difference from it is an unsaved edit), `opened` the stream's
// bytes and VM it was opened from, `flags` a preset's MAC-mode bits.
interface Edit { base: PacketDoc; doc: PacketDoc; flags: number | null; opened: { binary: string; vm: StreamJson['vm'] } }

// The selected stream's packet: as information (the summary, its size, the length of its field engine program, a viewer
// and this stream's export: pcap written in the browser from the stored bytes, R2; YAML from the backend), or, after
// Edit packet, in the embedded packet editor with Presets…, Cancel and Save. A row with `length` null has no usable packet.
// Save is one write through the profile's `writer`, and its undo entry goes to `history` (R11). `onDirty` hears whether
// the editor holds unsaved edits; `onSaved` runs when a Save has landed. `disabled`: the table's lock or an ended writer.
export function PacketPane({ slug, row, writer, history, onSaved, onDirty, disabled = false, active = true }: {
  slug: string; row: StreamRow; writer?: ProfileWriter; history?: UndoHistory; onSaved?: () => void; onDirty?: (dirty: boolean) => void
  disabled?: boolean; active?: boolean
}) {
  const [viewing, setViewing] = useState(false)
  const [menu, setMenu] = useState(false)
  const [edit, setEdit] = useState<Edit | null>(null)
  const [opening, setOpening] = useState(false)
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [presets, setPresets] = useState<{ origin: PacketDoc } | null>(null)
  const [discard, setDiscard] = useState<{ run: () => void } | null>(null)   // an action waiting for the user to agree to lose the edits
  const usable = row.length !== null
  const binary = row.stream.packet.binary
  const exportRef = useRef<HTMLDivElement>(null)
  const work = useRef(0)                      // bumped by Cancel and unmount: a reply of an older request is dropped
  const docRef = useRef<PacketDoc | null>(null)
  useEffect(() => { docRef.current = edit?.doc ?? null }, [edit?.doc])
  useEffect(() => () => { work.current += 1 }, [])

  const dirty = useMemo(() => edit !== null && (edit.flags !== null || JSON.stringify(edit.doc) !== JSON.stringify(edit.base)), [edit])
  const dirtyRef = useRef(dirty)
  useEffect(() => { dirtyRef.current = dirty; onDirty?.(dirty) }, [dirty, onDirty])
  useEffect(() => () => onDirty?.(false), [onDirty])

  // The Export menu closes on Escape and on a press outside it (button included, so the button's own click toggles).
  const closeMenu = useCallback(() => setMenu(false), [])
  useHotkey('escape', closeMenu, menu && active)
  useEffect(() => {
    if (!menu) return
    const down = (e: MouseEvent) => { const root = exportRef.current?.parentElement; if (root && !root.contains(e.target as Node)) setMenu(false) }
    document.addEventListener('mousedown', down)
    return () => document.removeEventListener('mousedown', down)
  }, [menu])

  // The viewer's row: the packet with no capture context.
  const decoded: DecodedRow = { index: row.index, ts_rel_us: 0, port: 0, origin: '', src: '', dst: '', proto: row.summary.proto, wirelen: row.length ?? 0, info: row.summary.info, binary }

  function exportPcap() {
    setMenu(false)
    if (row.length === null) return
    downloadBlob(`${slug}-${row.index}.pcap`, toPcap([{ binary, ts_rel_us: 0, wirelen: row.length }]))
  }

  async function startEdit() {
    const mine = ++work.current
    const opened = { binary, vm: row.stream.vm }
    setMenu(false); setOpening(true); setNotice(null)
    try {
      const r = await docForStream(slug, row)
      if (mine !== work.current) return
      setEdit({ base: r.doc, doc: r.doc, flags: null, opened })
      setNotice(r.warnings.length > 0 ? { kind: 'status', text: r.warnings.join(' ') } : null)
    } catch (x) {
      if (mine === work.current) setNotice({ kind: 'error', text: `Edit packet: ${errText(x)}` })
    } finally { if (mine === work.current) setOpening(false) }
  }

  // Leaving the editor asks first when it holds unsaved edits (in the app's own dialog, never the browser's).
  function close() {
    const run = () => { work.current += 1; setEdit(null); setPresets(null); setNotice(null) }
    if (dirtyRef.current) setDiscard({ run }); else run()
  }

  // A preset's document replaces the editor's. `origin` is the document the dialog was opened over: if the editor holds
  // another one now, the preset is stale and dropped (checked again when the user agrees to discard).
  function adopt(next: PacketDoc, flags: number | null, origin: PacketDoc) {
    const apply = () => {
      if (docRef.current !== origin) { setNotice({ kind: 'error', text: DROPPED }); setPresets(null); return }
      setEdit((e) => e && { ...e, doc: next, flags }); setPresets(null); setNotice(null)
    }
    if (docRef.current !== origin) { apply(); return }
    if (dirtyRef.current) setDiscard({ run: apply }); else apply()
  }

  // Builds the stream's packet and VM, then writes them (with the sidecar entry and possibly the flags) in the writer's
  // queue. At the write's turn the stream is read again: it must still be the one the editor opened (else nothing is
  // written), and what it held then is the undo's "before". An undo entry is pushed only when the reads were taken at the
  // rev the write was sent under (R11); otherwise the history is cleared.
  async function save() {
    if (!edit || !writer || !history || saving) return
    const sent = edit.doc
    const { flags, opened, base } = edit
    const index = row.index
    const mine = work.current
    setSaving(true); setNotice(null)
    try {
      const built = await toStream(sent)
      if (mine !== work.current) return
      if (docRef.current !== sent) { setNotice({ kind: 'error', text: 'The packet changed while it was being built: nothing was saved. Save again.' }); return }
      let writing = false
      const got: { before?: { stream: StreamJson; sidecar: SidecarEntry }; patch?: StreamPatch; after?: SidecarEntry; note?: string | null } = {}
      await writer.run(async (rev) => {
        const [page, entry] = await Promise.all([getStreams(slug, index, 1), streamSidecar(slug, index)])
        const live = page.rows[0]
        if (!live || live.index !== index || !same(live.stream.packet.binary, opened.binary) || !same(live.stream.vm, opened.vm)) throw new Error(CHANGED)
        if (page.rev === rev && entry.rev === rev) got.before = { stream: live.stream, sidecar: entry.entry }
        const diff = saveDiff(index, live.stream, sent, built, macEdits(base, sent), flags, rev)
        got.patch = (diff.changed as Record<string, StreamPatch>)[String(index)]
        got.after = (diff.sidecar as Record<string, SidecarEntry>)[String(index)]
        got.note = tooLarge(diff, index)
        writing = true
        return await putStreams(slug, diff)
      }, (x) => (writing ? endOf(x) : null))
      if (got.before && got.patch && got.after) history.push(undoPatch(index, got.before.stream, got.patch, got.before.sidecar, { afterSidecar: got.after }))
      else history.clear()
      onSaved?.()
      if (mine !== work.current) return
      // edits made while the write was out stay, against the document just saved
      setEdit((e) => (e && e.doc === sent ? null : e && { ...e, base: sent, flags: null, opened: { binary: built.packet.binary, vm: built.vm } }))
      setNotice({ kind: 'status', text: `Saved.${got.note ? ` Note: ${got.note}.` : ''}` })
    } catch (x) {
      if (mine === work.current && writer.getState() === 'ready') setNotice({ kind: 'error', text: errText(x) })
    } finally { if (mine === work.current) setSaving(false) }
  }

  const line = notice && <p role={notice.kind === 'error' ? 'alert' : 'status'} className={notice.kind === 'error' ? 'error' : 'muted'}>{notice.text}</p>
  const modals = (
    <>
      {presets && edit && (
        <ModalLayer>
          <PresetsDialog active={active && !discard} onClose={() => setPresets(null)} onOpen={(d, f) => adopt(d, f, presets.origin)} />
        </ModalLayer>
      )}
      {discard && <DiscardEditsDialog body="The packet has unsaved edits." onKeep={() => setDiscard(null)}
        onDiscard={() => { const { run } = discard; setDiscard(null); run() }} />}
    </>
  )

  if (edit) {
    return (
      <div className="packet-pane editing">
        <div className="setups-buttons" role="toolbar" aria-label="Edit packet">
          <button type="button" disabled={saving || disabled} onClick={() => setPresets({ origin: edit.doc })}>Presets…</button>
          <button type="button" disabled={saving} onClick={close}>Cancel</button>
          <button type="button" disabled={saving || disabled} onClick={() => void save()}>Save</button>
          {/* the packet as a whole differs from the one it was opened with (RC1-31) */}
          {dirty && <EditedTag />}
        </div>
        {line}
        <PacketEditor doc={edit.doc} onDocChange={(doc) => setEdit((e) => e && { ...e, doc })} mode="embedded" />
        {modals}
      </div>
    )
  }

  return (
    <div className="packet-pane">
      <p>{row.summary.proto} {row.summary.info}</p>
      {row.length !== null && <p>{row.length} bytes ({row.length + 4} with FCS)</p>}
      <p>Field engine: {plural(row.stream.vm.instructions.length, 'instruction')}</p>
      <div className="setups-buttons">
        <button type="button" disabled={!usable} onClick={() => setViewing(true)}>View packet…</button>
        <button type="button" disabled={!usable || !writer || !history || disabled || opening} onClick={() => void startEdit()}>Edit packet</button>
        <span className="setups-menu">
          <button type="button" aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu((m) => !m)}>Export ▾</button>
          {menu && (
            <div role="menu" aria-label="Export this stream" className="setups-menu-list export-menu" ref={exportRef}>
              <button type="button" role="menuitem" disabled={!usable} onClick={exportPcap}>pcap</button>
              <a role="menuitem" href={exportUrl(slug, 'yaml', row.index)} download onClick={() => setMenu(false)}>YAML</a>
            </div>
          )}
        </span>
      </div>
      {line}
      {viewing && <PacketViewer row={decoded} onClose={() => setViewing(false)} />}
      {modals}
    </div>
  )
}
