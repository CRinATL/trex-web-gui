import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { ApiError, exportUrl, getStreams, getTemplate, importProfile, simulate, type DiffReply, type ImportBody, type LibraryEntry, type SimReport, type StreamPage, type StreamPatch } from '../../api'
import { useHotkey } from '../../keys'
import { Tabs, tabPanel } from '../../ui/Tabs'
import { DiscardEditsDialog } from './DiscardEditsDialog'
import { ImportDialog } from './ImportDialog'
import { PacketPane } from './PacketPane'
import { addedText, pasteDiff } from './streamEdits'
import { SimulateDialog } from './SimulateDialog'
import { SourceDialog } from './SourceDialog'
import { PAGE_ROWS, PINNED, StreamTable, type Notice } from './StreamTable'
import { pointersTo, undoAdd, undoPatch, UndoHistory } from './streamUndo'
import { StreamProperties } from './StreamProperties'
import { endOf, ProfileWriter, type WriterState } from './studioWriter'

type DetailTab = 'properties' | 'packet'
const DETAIL_TABS: { id: DetailTab; label: string }[] = [{ id: 'properties', label: 'Properties' }, { id: 'packet', label: 'Packet' }]

const errText = (x: unknown) => (x instanceof ApiError || x instanceof Error ? x.message : String(x))
const noSubscribe = () => () => undefined
const ready = (): WriterState => 'ready'
// The library's timestamp format (store/test_setups.py _now), shown to the second.
const savedAt = (t: string) => `${t.replace('T', ' ').slice(0, 19)} UTC`

// The information line: source, when it was saved, how many streams.
function infoLine(entry: LibraryEntry, count: number): string {
  const s = entry.source
  const source = !s ? 'no source' : s.kind === 'new' ? 'new profile' : `${s.kind}${s.path ? ` ${s.path}` : ''}${entry.source_missing ? ' (source missing)' : ''}`
  return `Source: ${source} · saved ${savedAt(entry.updated)} · ${count} stream${count === 1 ? '' : 's'}`
}

// "Export ▾": downloads of the whole profile. A .py entry's Python is written from the frozen JSON, not its source.
// The import route's 404 for an expired upload (backend/trex_web_gui/api/studio.py:485 "No upload"): the profile is not gone.
const uploadGone = (x: unknown) => x instanceof ApiError && x.status === 404 && x.message.startsWith('No upload')

function ExportMenu({ slug, py, disabled, active }: { slug: string; py: boolean; disabled: boolean; active: boolean }) {
  const [open, setOpen] = useState(false)
  if (open && !active) setOpen(false)   // a hidden page closes its menu (adjusting state during render, as ProfilePicker does)
  const close = useCallback(() => setOpen(false), [])
  return (
    <span className="setups-menu">
      <button type="button" aria-haspopup="menu" aria-expanded={open} disabled={disabled} onClick={() => setOpen((o) => !o)}>Export ▾</button>
      {open && !disabled && <ExportList slug={slug} py={py} close={close} />}
    </span>
  )
}
function ExportList({ slug, py, close }: { slug: string; py: boolean; close: () => void }) {
  useHotkey('escape', close)       // mounted only while the menu is open
  const ref = useRef<HTMLDivElement>(null)
  // a press outside the whole menu (button included, so the button's own click can toggle) closes it
  useEffect(() => {
    const down = (e: MouseEvent) => {
      const root = ref.current?.parentElement
      if (root && !root.contains(e.target as Node)) close()
    }
    document.addEventListener('mousedown', down)
    return () => document.removeEventListener('mousedown', down)
  }, [close])
  return (
    <div role="menu" aria-label="Export" className="setups-menu-list" ref={ref}>
      <a role="menuitem" href={exportUrl(slug, 'json')} download onClick={close}>JSON</a>
      <a role="menuitem" href={exportUrl(slug, 'yaml')} download onClick={close}>YAML</a>
      <a role="menuitem" href={exportUrl(slug, 'py')} download onClick={close}>{py ? 'Python (from the frozen JSON, not the .py source)' : 'Python'}</a>
    </div>
  )
}

// One library profile: its information line, its action bar, the stream table, then the selected stream's detail
// (Task 9). Every table action is saved at once through one ProfileWriter (R5); a stale or gone writer ends editing
// until Reload. `onChanged` re-reads the library (after a write, and from Reload library).
// `active` false: the Studio page is hidden, so its dialogs and the Export menu take no keys (they stay as they were).
// The packet pane's embedded editor may hold unsaved edits: switching stream or detail tab asks first, and `onUnsaved`
// tells the page, which asks before another profile is selected.
const MODIFIER = /Mac/.test(navigator.platform) ? '⌘' : 'Ctrl'

export function ProfileEditor({ entry, onChanged, onUnsaved, active = true }: { entry: LibraryEntry; onChanged: () => void; onUnsaved?: (unsaved: boolean) => void; active?: boolean }) {
  const [offset, setOffset] = useState(0)
  const [reads, setReads] = useState(0)          // bumped (through `reread`) to read the page again
  const readGen = useRef(0)                      // the latest `reads`, readable at once
  const [page, setPage] = useState<StreamPage | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [writer, setWriter] = useState<ProfileWriter | null>(null)
  const [selection, setSelection] = useState<number[]>([])
  const [notice, setNotice] = useState<Notice | null>(null)
  const [adding, setAdding] = useState(false)
  // An index-shifting write is queued or in flight, or has landed and no page read started after it has (review I1).
  // The lock is owned per write: each shifting write (a table reorder, duplicate, paste or delete, Add stream, the pcap
  // append) takes a claim when it is sent. A refusal gives back only its own claim; a success turns its claim into
  // "a page read started after this reply must land". `shifting` is on while any claim is out or any such read is due.
  const [shifting, setShifting] = useState(false)
  const claims = useRef(0)
  // Reload's generation: each claim records the one it was taken in, and once Reload has started a new one, that
  // claim's release (a refusal's give-back, a success's "re-read due") does nothing, so a write from before Reload
  // never frees a newer write's claim (final review I2). `gen` is the same value for render: the table's callbacks
  // are bound to the generation it was shown in.
  const writerGen = useRef(0)
  const [gen, setGen] = useState(0)
  // The `reads` value of the first read started after the latest successful shifting write: only a read of that
  // generation or later meets every landed write's condition, never one that was already out (re-review N1).
  const unlockAt = useRef<number | null>(null)
  const [confirming, setConfirming] = useState(false)     // the table's delete confirm is open
  const [detailTab, setDetailTab] = useState<DetailTab>('properties')
  const [appending, setAppending] = useState(false)      // the pcap append dialog is open
  const pageRef = useRef<StreamPage | null>(null)         // the last page read, for a queued write's turn
  const [report, setReport] = useState<SimReport | null>(null)
  const [simulating, setSimulating] = useState(false)
  const [viewing, setViewing] = useState(false)           // the .py source dialog is open
  // The stream table's undo history (D11). Writes made outside the table record themselves here; a stale or gone writer,
  // Reload and the pcap append (whose streams the editor never sees) empty it.
  const [history] = useState(() => new UndoHistory())
  const [packetDirty, setPacketDirty] = useState(false)   // the packet pane's editor holds unsaved edits
  const [leaving, setLeaving] = useState<{ go: () => void } | null>(null)   // a switch waiting for the user to agree to lose them
  const [packetEpoch, setPacketEpoch] = useState(0)          // bumped when the edits are discarded: the pane starts again
  const state = useSyncExternalStore(writer?.subscribe ?? noSubscribe, writer?.getState ?? ready)
  const slug = entry.slug

  useEffect(() => { if (state !== 'ready') history.clear() }, [state, history])
  useEffect(() => { onUnsaved?.(packetDirty) }, [packetDirty, onUnsaved])
  useEffect(() => () => onUnsaved?.(false), [onUnsaved])
  // Runs `go` now, or once the user agrees to discard the packet edits.
  const leave = (go: () => void) => { if (packetDirty) setLeaving({ go }); else go() }
  // A write lands after the render that started it, so `written` asks through a ref (review I1).
  const dirtyRef = useRef(false)
  useEffect(() => { dirtyRef.current = packetDirty }, [packetDirty])
  const selectionRef = useRef<number[]>([])
  useEffect(() => { selectionRef.current = selection }, [selection])

  function relock() { setShifting(claims.current > 0 || unlockAt.current !== null) }

  useEffect(() => {
    let live = true
    const gen = reads
    getStreams(slug, offset).then((p) => {
      if (!live) return
      // a delete can leave the page past the end: show the last page instead
      if (p.rows.length === 0 && p.offset > 0) { setOffset(Math.floor(Math.max(0, p.stream_count - 1) / PAGE_ROWS) * PAGE_ROWS); return }
      setPage(p); setLoadError(null); pageRef.current = p
      if (unlockAt.current !== null && gen >= unlockAt.current) { unlockAt.current = null; relock() }
      setWriter((w) => w ?? new ProfileWriter(slug, p.rev))     // the first read (and the read after Reload) starts one
    }, (x: unknown) => { if (live) setLoadError(errText(x)) })
    return () => { live = false }
  }, [slug, offset, reads])

  // After a saved write: select `select` (undefined keeps the selection), showing the page that holds its first index.
  // Runs when the write lands, so it reads no state captured when the write was started.
  function reread() { readGen.current += 1; setReads(readGen.current) }
  // `claimGen`: the generation the write's claim was taken in (a write that took none may pass the current one).
  function written(select: number[] | undefined, shifted: boolean, claimGen: number) {
    if (select) {
      // The table's actions that move the selection are off while the editor holds unsaved edits, so this is a write that
      // was already out when they were made: it asks before the pane is unmounted, and "Keep editing" leaves the selection
      // (a Save is refused for a stream that moved away from its index).
      const move = () => {
        setSelection(select)
        if (select.length) setOffset(Math.floor(Math.min(...select) / PAGE_ROWS) * PAGE_ROWS)
      }
      const here = selectionRef.current
      if (dirtyRef.current && !(select.length === here.length && select.every((v, i) => v === here[i]))) setLeaving({ go: move })
      else move()
    }
    reread()
    if (shifted && claimGen === writerGen.current) { claims.current = Math.max(0, claims.current - 1); unlockAt.current = readGen.current; relock() }
    onChanged()
  }
  // `on`: a shifting write is being sent (it takes a claim); off: that write was refused (it gives its claim back).
  // Either does nothing for a write of an earlier generation than `writerGen`'s.
  function shift(on: boolean, claimGen: number) {
    if (claimGen !== writerGen.current) return
    claims.current = on ? claims.current + 1 : Math.max(0, claims.current - 1); relock()
  }
  function reload() {
    // a new writer: whatever the old one held is void (its queued writes are refused at once, and give back nothing below 0)
    history.clear()
    claims.current = 0; unlockAt.current = null; setShifting(false)
    writerGen.current += 1; setGen(writerGen.current)      // every claim still out is of the old generation
    setWriter(null); setNotice(null); setSelection([]); setConfirming(false)   // the table remounts, confirm closed
    reread()
  }
  async function addStream() {
    if (!writer || !page) return
    const claimGen = writerGen.current
    setAdding(true); setNotice(null); shift(true, claimGen)
    try {
      const tpl = await getTemplate()
      const r = await writer.write((rev) => pasteDiff(0, null, [tpl], rev))
      history.push(undoAdd(r.stream_count - 1, [tpl]))
      written([r.stream_count - 1], true, claimGen)
    } catch (x) {
      shift(false, claimGen)
      if (writer.getState() === 'ready') setNotice({ kind: 'error', text: errText(x) })
    } finally { setAdding(false) }
  }

  // A properties Save is an index-based write (changed: {index: patch}), so it waits out the same lock as the table's edits.
  // Resolves true when the write landed (the form then waits for the page read after it), false when it did not.
  async function saveProperties(index: number, patch: StreamPatch): Promise<boolean> {
    if (!writer) return false
    setNotice(null)
    const before = pageRef.current?.rows.find((r) => r.index === index)?.stream
    // the shown page lags a queued or landed write unless nothing is pending and it was read at the writer's rev
    const current = writer.pending === 0 && pageRef.current?.rev === writer.rev
    const old = before?.name
    // a name removed clears the `next` that named it; the page's streams that did are put back by the undo
    const pointers = patch.name === null && old !== undefined ? pointersTo(pageRef.current?.rows ?? [], old, index) : {}
    try {
      const r = await writer.write((rev) => ({ rev, changed: { [String(index)]: patch } }))
      if (current && before && r.cleared.length === Object.keys(pointers).length) history.push(undoPatch(index, before, patch, undefined, { pointers }))
      else history.clear()
      written(undefined, false, writerGen.current)
      return true
    } catch (x) {
      if (writer.getState() === 'ready') setNotice({ kind: 'error', text: errText(x) })
      return false
    }
  }
  // The pcap append is one more write in the writer's queue, built from the rev current at its turn, and a shifting one:
  // the lock holds from its sending until the page read after it lands, and a refusal releases it. A stale or gone
  // reply ends the writer as a table write does (and closes the dialog, so the editor's alert is the one line); any
  // other refusal stays on the dialog's error line. The dialog may be closed while the append is out: it still lands.
  async function sendAppend(build: (rev: string) => ImportBody): Promise<DiffReply> {
    if (!writer) throw new Error('The profile is not loaded yet.')
    const claimGen = writerGen.current
    setNotice(null); shift(true, claimGen)
    let from = 0
    try {
      const r = await writer.run(async (rev) => {
        from = writer.count ?? pageRef.current?.stream_count ?? 0     // every earlier write has landed by now
        return await importProfile(build(rev)) as DiffReply
      }, (x) => (uploadGone(x) ? null : endOf(x)))
      history.clear()          // the editor never saw the appended streams, so it cannot take them out or put them back
      const n = r.stream_count - from
      setNotice({ kind: 'status', text: [`Appended ${n} stream${n === 1 ? '' : 's'} from the pcap.`, addedText('Appended', r)].filter(Boolean).join(' ') })
      written(Array.from({ length: n }, (_, i) => from + i), true, claimGen)
      return r
    } catch (x) {
      shift(false, claimGen)
      if (writer.getState() !== 'ready') setAppending(false)
      throw x
    }
  }
  async function doSimulate() {
    setSimulating(true); setNotice(null)
    try { setReport(await simulate(slug)) } catch (x) { setNotice({ kind: 'error', text: errText(x) }) } finally { setSimulating(false) }
  }

  function goTo(index: number) {
    leave(() => {
      setSelection([index])
      setOffset(Math.floor(index / PAGE_ROWS) * PAGE_ROWS)
    })
  }

  const ended = state !== 'ready'
  const locked = ended || shifting || confirming
  const picked = selection.length === 1 ? page?.rows.find((r) => r.index === selection[0]) : undefined
  const s = entry.source
  return (
    <div className="profile-editor">
      <div className="profile-info">
        <p className="muted">{infoLine(entry, page?.stream_count ?? entry.stream_count)}</p>
        {s?.kind === 'py' && <p className="muted">Control assigns the .py source with its tunables; edits here are not sent.</p>}
        {s?.ports_differ && <p className="muted">Imported for port 0; port 1's streams differ.</p>}
      </div>
      <div className="setups-buttons" role="toolbar" aria-label="Profile actions">
        <button type="button" disabled={ended || !writer || adding || shifting || confirming || packetDirty} title={packetDirty ? PINNED : undefined} onClick={() => void addStream()}>Add stream</button>
        <button type="button" disabled={ended || !writer || !page || adding || shifting || confirming || packetDirty} title={packetDirty ? PINNED : undefined} onClick={() => { setNotice(null); setAppending(true) }}>Import stream from pcap…</button>
        <ExportMenu slug={slug} py={s?.kind === 'py'} disabled={state === 'gone'} active={active} />
        <button type="button" disabled={state === 'gone' || simulating} onClick={() => void doSimulate()}>Simulate</button>
        {s?.kind === 'py' && <button type="button" disabled={state === 'gone'} onClick={() => setViewing(true)}>View source…</button>}
      </div>
      {state === 'stale' && (
        <p role="alert" className="error">This profile changed elsewhere. <button type="button" onClick={() => leave(reload)}>Reload</button></p>
      )}
      {state === 'gone' && (
        <p role="alert" className="error">This profile was renamed or deleted elsewhere. <button type="button" onClick={onChanged}>Reload library</button></p>
      )}
      {!ended && loadError !== null && (
        <p role="alert" className="error">{loadError} <button type="button" onClick={reread}>Reload</button></p>
      )}
      {!ended && loadError === null && notice?.kind === 'error' && <p role="alert" className="error">{notice.text}</p>}
      {!ended && !loadError && notice?.kind === 'status' && <p role="status">{notice.text}</p>}
      {page && writer && (
        <StreamTable page={page} selection={selection} onSelect={(sel) => (sel.length === selection.length && sel.every((v, i) => v === selection[i]) ? setSelection(sel) : leave(() => setSelection(sel)))} writer={writer} history={history} onWritten={(select, shifted) => written(select, shifted, gen)} onShift={(on) => shift(on, gen)} onConfirming={setConfirming}
          onOffset={(o) => leave(() => { setSelection([]); setOffset(o) })} onNotice={setNotice} shifting={shifting} disabled={ended} pinned={packetDirty} />
      )}
      {page && writer && !picked && (
        <div className="stream-placeholder">
          {selection.length > 1 ? (
            <p>{selection.length} streams selected. Click one to edit it.</p>
          ) : (
            <>
              <p>Click a stream to edit its properties and packet.</p>
              <p className="muted">{MODIFIER}-click or Shift-click selects several, for Copy, Duplicate and Delete.</p>
            </>
          )}
        </div>
      )}
      {page && writer && picked && (
        <div className="stream-detail">
          <Tabs label="Stream" idPrefix="stream" tabs={DETAIL_TABS} selected={detailTab} onSelect={(t) => leave(() => setDetailTab(t))} />
          <div {...tabPanel('stream', 'properties', detailTab)}>
            <StreamProperties stream={picked.stream} index={picked.index} names={page.names} disabled={locked}
              onSave={(patch) => saveProperties(picked.index, patch)}
              onPrev={!locked && picked.index > 0 ? () => goTo(picked.index - 1) : undefined}
              onNext={!locked && picked.index < page.stream_count - 1 ? () => goTo(picked.index + 1) : undefined} />
          </div>
          <div {...tabPanel('stream', 'packet', detailTab)}>
            <PacketPane key={`${picked.index}:${packetEpoch}`} slug={slug} row={picked} writer={writer} history={history} disabled={locked} active={active && !leaving}
              onDirty={setPacketDirty} onSaved={() => written(undefined, false, writerGen.current)} />
          </div>
        </div>
      )}
      {appending && writer && <ImportDialog appendTo={{ slug, send: sendAppend }} blocked={shifting} active={active} onClose={() => setAppending(false)} onDone={() => setAppending(false)} />}
      {viewing && <SourceDialog slug={slug} active={active} onClose={() => setViewing(false)} />}
      {leaving && <DiscardEditsDialog body="The packet has unsaved edits." onKeep={() => setLeaving(null)}
        onDiscard={() => { const { go } = leaving; setLeaving(null); setPacketEpoch((n) => n + 1); go() }} />}
      {report && <SimulateDialog report={report} active={active} onClose={() => setReport(null)} />}
    </div>
  )
}
