import { useRef, useState, useSyncExternalStore, type KeyboardEvent, type MouseEvent } from 'react'
import { ApiError, type DiffReply, type SidecarEntry, type StreamJson, type StreamPage, type StreamRow, type StreamsDiff } from '../../api'
import { humanBps, humanPps } from '../../statsFormat'
import { copyStreams, pastedEntries, pastedStreams } from './streamClipboard'
import { nameError } from './streamForm'
import { addedText, deleteDiff, duplicateDiff, pasteDiff, renameDiff, reorderDiff, toggleDiff } from './streamEdits'
import { fetchEntries, pointersTo, undoAdd, undoDelete, undoRename, undoReorder, undoToggle, type UndoEntry, type UndoHistory } from './streamUndo'
import type { ProfileWriter } from './studioWriter'

export const PAGE_ROWS = 100       // the backend's PAGE_MAX
export type Notice = { kind: 'status' | 'error'; text: string }

const errText = (x: unknown) => (x instanceof ApiError || x instanceof Error ? x.message : String(x))
const label = (s: StreamJson, index: number) => (s.name === undefined ? `#${index}` : String(s.name))
const nameOf = (name: string | number | null, index: number) => (name === null ? `#${index}` : String(name))
const plural = (n: number, what: string) => `${n} ${what}${n === 1 ? '' : 's'}`
const NOT_COPIED = ' (editor documents not copied)'
// Whether a row with an editor document has no entry in `entries` (its fetch failed or the 8 MiB budget ran out).
const droppedDocs = (rows: StreamRow[], entries: Record<number, SidecarEntry>) =>
  rows.some((r) => r.has_editor && (entries[r.index] === undefined || Object.keys(entries[r.index]).length === 0))
const range = (from: number, n: number) => Array.from({ length: n }, (_, i) => from + i)

function modeText(m: StreamJson['mode']): string {
  if (m.type === 'single_burst') return `single burst, ${m.total_pkts} pkts`
  if (m.type === 'multi_burst') return `multi burst, ${m.count} × ${m.pkts_per_burst} pkts`
  return 'continuous'
}
function rateText(r: StreamJson['mode']['rate']): string {
  if (r.type === 'pps') return humanPps(r.value)
  if (r.type === 'percentage') return `${r.value} %`
  return `${humanBps(r.value)} ${r.type === 'bps_L1' ? 'L1' : 'L2'}`
}

// The stream table (A5.6): one page of rows, multi-select, inline rename, and its own action bar. Every action is one
// diff saved at once through `writer` (R5). Its keys work only while the table has focus, never through useHotkey
// (spec §6.2). `onWritten(select, shifted)` follows each saved write: re-read the page and select those indices
// (undefined keeps the selection). `onSelect` sets the selection at once, before any write lands.
// An index-shifting write (reorder, duplicate, paste, delete) calls `onShift(true)` before it is queued and
// `onShift(false)` if it is refused; the editor then sets `shifting` until the page read after it lands. While
// `shifting`, every index-based input is disabled: a diff built from the shown page's indices would otherwise be sent
// with the newer rev and land on the wrong stream (review I1). `disabled`: the writer has ended (stale or gone).
// An open delete confirm holds indices too, so while it is open every index-shifting action is blocked (and
// `onConfirming` tells the editor, which blocks Add stream); a shifting write that starts anyway closes it. It is also
// told while sidecar entries are fetched for a copy, duplicate or delete, so nothing shifts the indices meanwhile.
// `history` (D11, owned by the editor, which also records its own writes in it): Undo and Redo send the inverse diff
// through `writer`, only while no write is queued or in flight and no index-shifting one is out; an entry is pushed only
// after its write resolved OK.
// `pinned`: the packet pane holds unsaved edits, and a write that moves the selection (or the open stream's index) would
// unmount them; the actions that do are off, with this as their title.
export const PINNED = 'The packet has unsaved edits: save or cancel them first.'

export function StreamTable({ page, selection, onSelect, writer, history, onWritten, onShift, onConfirming, onOffset, onNotice, shifting = false, disabled = false, pinned = false }: {
  page: StreamPage; selection: number[]; onSelect: (indices: number[]) => void; writer: ProfileWriter; history: UndoHistory
  onWritten: (select: number[] | undefined, shifted: boolean) => void; onShift: (on: boolean) => void; onConfirming: (open: boolean) => void
  onOffset: (offset: number) => void; onNotice: (n: Notice | null) => void; shifting?: boolean; disabled?: boolean; pinned?: boolean
}) {
  const locked = disabled || shifting
  const stay = pinned ? PINNED : undefined      // title of an action that is off while the selection is pinned
  const [renaming, setRenaming] = useState<{ index: number; text: string } | null>(null)
  const [confirm, setConfirm] = useState<number[] | null>(null)
  const [busy, setBusy] = useState(false)          // sidecar entries are being fetched for an action
  const working = useRef(false)
  const pending = useSyncExternalStore(writer.subscribePending, () => writer.pending)
  useSyncExternalStore(history.subscribe, history.getVersion)
  const ask = (indices: number[] | null) => { setConfirm(indices); onConfirming(indices !== null) }
  // A toggled checkbox shows its new state until the next page read; kept per page object, so a read clears it.
  const [enabledNow, setEnabledNow] = useState<{ page: StreamPage; values: Record<number, boolean> }>({ page, values: {} })
  const anchor = useRef<number | null>(null)
  const tableRef = useRef<HTMLTableElement>(null)
  const count = page.stream_count
  const sel = selection.filter((i) => page.rows.some((r) => r.index === i))
  const selectedRows = page.rows.filter((r) => sel.includes(r.index))
  const enabledOf = (r: StreamRow) => (enabledNow.page === page ? enabledNow.values[r.index] : undefined) ?? r.stream.enabled

  // One write. A stale or gone writer is the editor's alert, so it is not repeated here as an error line.
  // `shift`: the write moves indices. `failed` undoes what was shown ahead of the write.
  async function save(build: (rev: string) => StreamsDiff, o: {
    shift?: boolean; done?: (r: DiffReply) => { notice?: string; select?: number[] }; failed?: () => void
    record?: (r: DiffReply) => UndoEntry | null     // the undo entry of a write that landed (null: the history no longer matches, so it is cleared)
  } = {}) {
    onNotice(null)
    if (o.shift) { ask(null); onShift(true) }
    try {
      const r = await writer.write(build)
      if (o.record) { const e = o.record(r); if (e) history.push(e); else history.clear() }
      const after = o.done?.(r) ?? {}
      if (after.notice) onNotice({ kind: 'status', text: after.notice })
      onWritten(after.select, !!o.shift)
    } catch (x) {
      if (o.shift) onShift(false)
      o.failed?.()
      if (writer.getState() === 'ready') onNotice({ kind: 'error', text: errText(x) })
    }
  }

  function click(e: MouseEvent, index: number) {
    if (locked) return
    if (e.shiftKey && anchor.current !== null) {
      const [lo, hi] = [Math.min(anchor.current, index), Math.max(anchor.current, index)]
      onSelect(range(lo, hi - lo + 1))
      return
    }
    anchor.current = index
    if (e.ctrlKey || e.metaKey) onSelect(sel.includes(index) ? sel.filter((i) => i !== index) : [...sel, index])
    else onSelect([index])
  }
  function toggle(r: StreamRow) {
    const before = enabledOf(r)
    const enabled = !before
    setEnabledNow((cur) => ({ page, values: { ...(cur.page === page ? cur.values : {}), [r.index]: enabled } }))
    const putBack = () => setEnabledNow((cur) => {
      if (cur.page !== page) return cur
      const values = { ...cur.values }
      delete values[r.index]
      return { page, values }
    })
    void save((rev) => toggleDiff(r.index, enabled, rev), { failed: putBack, record: () => undoToggle(r.index, before) })
  }
  function startRename() {
    if (sel.length !== 1) return
    const r = selectedRows[0]
    setRenaming({ index: r.index, text: r.stream.name === undefined ? '' : String(r.stream.name) })
  }
  function endRename(keep: boolean) {
    const r = renaming && page.rows.find((x) => x.index === renaming.index)
    setRenaming(null)
    tableRef.current?.focus()
    if (!keep || !r || !renaming) return
    const text = renaming.text.trim()
    const name = text === '' ? null : text
    if (name === (r.stream.name === undefined ? null : String(r.stream.name))) return
    const problem = nameError(text, page.names, r.stream.name)
    if (problem) { onNotice({ kind: 'error', text: `Name of stream ${r.index}: ${problem}` }); return }
    const old = r.stream.name === undefined ? null : r.stream.name
    const current = snapshotCurrent()
    // a rename to nothing clears the `next` that named it; the page's streams that did are put back by the undo
    const pointers = name === null && old !== null ? pointersTo(page.rows, old, r.index) : {}
    void save((rev) => renameDiff(r.index, name, rev), {
      record: (reply) => (current && reply.cleared.length === Object.keys(pointers).length ? undoRename(r.index, old, name, pointers) : null),
    })
  }
  function move(dir: -1 | 1) {
    if (reorderDiff(count, sel, dir, '') === null) return
    const moved = sel.map((i) => i + dir)
    const picked = [...sel]
    onSelect(moved)                 // at once, so a second move queued behind this one moves the same streams
    const from = anchor.current
    if (from !== null) anchor.current = from + dir
    const order = (reorderDiff(count, picked, dir, '') as StreamsDiff).order as number[]
    void save((rev) => reorderDiff(count, picked, dir, rev) as StreamsDiff, {
      shift: true, done: () => ({ select: moved }), failed: () => { onSelect(picked); anchor.current = from },
      record: () => undoReorder(order),
    })
  }
  // The shown page is the state every earlier write left: none is queued or in flight and the page was read at the
  // writer's rev. An undo entry built from a page that lags a queued or landed write would restore stale values, so an
  // action records only when this holds as it is started, and otherwise clears the history.
  const snapshotCurrent = () => writer.pending === 0 && page.rev === writer.rev

  // `go(entries)` with the sidecar entries of those of `rows` that have an editor document, by index. With none that has
  // one it runs at once; otherwise after they are fetched, with the editor told so that nothing shifts the indices.
  function withEntries(rows: StreamRow[], go: (entries: Record<number, SidecarEntry>) => void) {
    if (!rows.some((r) => r.has_editor)) { go({}); return }
    if (working.current) return
    const confirmOpen = confirm !== null            // a delete confirm that is open stays open, and still blocks the editor
    working.current = true; setBusy(true); onConfirming(true)
    void fetchEntries(writer.slug, rows).then((entries) => {
      working.current = false; setBusy(false); onConfirming(confirmOpen)
      go(entries)
    })
  }
  function duplicate() {
    const picked = [...selectedRows]
    withEntries(picked, (entries) => {
      const diff = duplicateDiff(page.rows, sel, count, '', entries)
      if (!diff) return
      const after = Math.max(...sel)
      const dropped = droppedDocs(picked, entries)
      void save((rev) => ({ ...diff, rev }), {
        shift: true, done: (r) => ({ notice: [addedText('Duplicated', r), dropped ? NOT_COPIED.trim() : ''].filter(Boolean).join(' ') || undefined, select: range(after + 1, sel.length) }),
        record: () => undoAdd(count, diff.added as StreamJson[], after + 1, diff.added_sidecar),
      })
    })
  }
  function copy() {
    if (selectedRows.length === 0) return
    const rows = [...selectedRows].sort((a, b) => a.index - b.index)
    withEntries(rows, (entries) => {
      copyStreams(rows.map((r) => r.stream), rows.map((r) => entries[r.index] ?? {}))
      onNotice({ kind: 'status', text: `Copied ${plural(rows.length, 'stream')}.${droppedDocs(rows, entries) ? NOT_COPIED : ''}` })
    })
  }
  function paste() {
    const streams = pastedStreams()
    if (streams.length === 0) { onNotice({ kind: 'status', text: 'Nothing to paste: copy streams first.' }); return }
    const entries = pastedEntries()
    const after = sel.length ? Math.max(...sel) : null
    const first = after === null ? count : after + 1
    void save((rev) => pasteDiff(count, after, streams, rev, entries), {
      shift: true, done: (r) => ({ notice: addedText('Pasted', r), select: range(first, streams.length) }),
      record: () => undoAdd(count, streams, first, entries),
    })
  }
  function remove(indices: number[]) {
    tableRef.current?.focus()
    const rows = page.rows.filter((r) => indices.includes(r.index))
    withEntries(rows, (entries) => {
      const current = snapshotCurrent()
      // the streams (kept ones, on this page) whose `next` names a deleted stream: the delete clears it, the undo restores it
      const names = new Map(rows.filter((r) => r.stream.name !== undefined).map((r) => [r.stream.name as string | number, r.index]))
      const cleared: Record<number, string | number> = {}
      for (const r of page.rows) if (!indices.includes(r.index) && r.stream.next !== undefined && names.has(r.stream.next)) cleared[r.index] = r.stream.next
      void save((rev) => deleteDiff(indices, rev), {
        shift: true, done: (r) => ({
          notice: r.cleared.length ? `${r.cleared.join(', ')} now stop instead of going to the deleted stream.` : undefined, select: [],
        }),
        // a `next` on another page would not be put back: the history then no longer matches
        record: (r) => (current && rows.length === indices.length && r.cleared.length === Object.keys(cleared).length ? undoDelete(rows, entries, cleared, r.stream_count) : null),
      })
    })
  }

  // Undo or redo the editor's latest entry: its inverse diff is one more write, built from the rev current at its turn.
  async function step(dir: 'undo' | 'redo') {
    const e = dir === 'undo' ? history.nextUndo() : history.nextRedo()
    if (!e || writer.pending > 0 || locked || confirm !== null || working.current || pinned) return
    onNotice(null)
    if (e.shifts) { ask(null); onShift(true) }
    try {
      const r = await writer.write(dir === 'undo' ? e.undo : e.redo)
      if (dir === 'undo') history.undid(); else history.redid()
      onNotice({ kind: 'status', text: [`${dir === 'undo' ? 'Undid' : 'Redid'} ${e.label}.`, addedText(dir === 'undo' ? 'Restored' : 'Added', r)].filter(Boolean).join(' ') })
      onWritten(e.shifts ? [] : undefined, e.shifts)
    } catch (x) {
      if (e.shifts) onShift(false)
      if (writer.getState() === 'ready') onNotice({ kind: 'error', text: errText(x) })
    }
  }

  function onKey(e: KeyboardEvent<HTMLTableElement>) {
    const typing = e.target instanceof HTMLInputElement && e.target.type !== 'checkbox'
    if (locked || renaming || typing) return
    const asking = confirm !== null
    const ctrl = e.ctrlKey || e.metaKey
    const key = e.key.toLowerCase()
    let handled = true
    if (e.altKey && e.key === 'ArrowUp') { if (!asking && !pinned) move(-1) }
    else if (e.altKey && e.key === 'ArrowDown') { if (!asking && !pinned) move(1) }
    else if (ctrl && key === 'z' && !e.shiftKey) { if (!asking) void step('undo') }
    else if (ctrl && (key === 'y' || (key === 'z' && e.shiftKey))) { if (!asking) void step('redo') }
    else if (ctrl && key === 'c') copy()
    else if (ctrl && key === 'v') { if (!asking && !pinned) paste() }
    else if (e.key === 'Delete') { if (!asking && !pinned && sel.length) ask([...sel]) }
    else if (e.key === 'F2') startRename()
    else handled = false
    if (handled) e.preventDefault()
  }

  const last = page.offset + page.rows.length
  const none = sel.length === 0
  const noShift = locked || confirm !== null || busy || pinned
  const quiet = !noShift && pending === 0
  const nextUndo = history.nextUndo(), nextRedo = history.nextRedo()
  const undoStays = pinned && !!nextUndo, redoStays = pinned && !!nextRedo
  return (
    <div className="stream-table-block">
      <div className="setups-buttons" role="toolbar" aria-label="Stream actions">
        <button type="button" disabled={!quiet || !nextUndo || undoStays} title={undoStays ? PINNED : nextUndo ? `Undo ${nextUndo.label} (Ctrl+Z)` : undefined} onClick={() => void step('undo')}>Undo</button>
        <button type="button" disabled={!quiet || !nextRedo || redoStays} title={redoStays ? PINNED : nextRedo ? `Redo ${nextRedo.label} (Ctrl+Y)` : undefined} onClick={() => void step('redo')}>Redo</button>
        <button type="button" disabled={noShift || reorderDiff(count, sel, -1, '') === null} title={stay} onClick={() => move(-1)}>Up</button>
        <button type="button" disabled={noShift || reorderDiff(count, sel, 1, '') === null} title={stay} onClick={() => move(1)}>Down</button>
        <button type="button" disabled={noShift || none} title={stay} onClick={duplicate}>Duplicate</button>
        <button type="button" disabled={shifting || busy || none} onClick={copy}>Copy</button>
        <button type="button" disabled={noShift} title={stay} onClick={paste}>Paste</button>
        <button type="button" disabled={noShift || none} title={stay} onClick={() => ask([...sel])}>Delete</button>
      </div>
      {confirm && (
        <p role="group" aria-label="Delete streams">Delete {plural(confirm.length, 'stream')}?{' '}
          <button type="button" className="danger" autoFocus disabled={locked || busy || pinned} title={stay} onClick={() => remove(confirm)}>Confirm delete</button>{' '}
          <button type="button" onClick={() => { ask(null); tableRef.current?.focus() }}>Cancel</button></p>
      )}
      {page.warnings.map((w) => (
        <p key={w.index} role="status">{nameOf(w.name, w.index)} goes to {String(w.next)}, which is disabled: TRex refuses this profile at assign until {nameOf(w.name, w.index)}'s Next changes.</p>
      ))}
      <div className="stream-table-frame">
      <div className="stream-table-scroll">
        <table className="stream-table" aria-label="Streams" tabIndex={0} ref={tableRef} onKeyDown={onKey}>
          <thead>
            <tr><th>Enabled</th><th>#</th><th>Name</th><th>Packet</th><th>Length (with FCS)</th><th>Mode</th><th>Rate</th><th>Next</th><th aria-hidden="true" /></tr>
          </thead>
          <tbody>
            {page.rows.map((r) => (
              <tr key={r.index} aria-selected={sel.includes(r.index)} data-open={sel.length === 1 && sel[0] === r.index ? 'true' : undefined} onClick={(e) => click(e, r.index)}>
                <td><input type="checkbox" aria-label={`Enabled ${label(r.stream, r.index)}`} checked={enabledOf(r)} disabled={locked}
                  onClick={(e) => e.stopPropagation()} onChange={() => toggle(r)} /></td>
                <td>{r.index}</td>
                {renaming?.index === r.index ? (
                  <td><input autoFocus aria-label={`Name of stream ${r.index}`} value={renaming.text} onClick={(e) => e.stopPropagation()}
                    onChange={(e) => setRenaming({ index: r.index, text: e.target.value })}
                    onKeyDown={(e) => { if (e.key === 'Enter') endRename(true); if (e.key === 'Escape') endRename(false); e.stopPropagation() }}
                    onBlur={() => endRename(false)} /></td>
                ) : (
                  <td onDoubleClick={() => { if (!locked) setRenaming({ index: r.index, text: r.stream.name === undefined ? '' : String(r.stream.name) }) }}>
                    {r.stream.name === undefined ? '' : String(r.stream.name)}</td>
                )}
                <td className={r.length === null ? 'error' : undefined}>{r.summary.proto} {r.summary.info}</td>
                <td>{r.length === null ? '—' : r.length + 4}</td>
                <td>{modeText(r.stream.mode)}</td>
                <td>{rateText(r.stream.mode.rate)}</td>
                <td>{r.stream.next === undefined ? '' : String(r.stream.next)}</td>
                <td aria-hidden="true" className="chev">›</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      </div>
      <div className="setups-buttons">
        <button type="button" disabled={page.offset === 0} onClick={() => onOffset(Math.max(0, page.offset - PAGE_ROWS))}>Previous</button>
        <span className="muted">{count === 0 ? 'No streams.' : `rows ${page.offset + 1}–${last} of ${count}`}</span>
        <button type="button" disabled={last >= count} onClick={() => onOffset(page.offset + PAGE_ROWS)}>Next</button>
      </div>
    </div>
  )
}
