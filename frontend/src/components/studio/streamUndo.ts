import { streamSidecar, type SidecarEntry, type StreamJson, type StreamPatch, type StreamRow, type StreamsDiff } from '../../api'
import { deleteDiff, pasteDiff, renameDiff, toggleDiff } from './streamEdits'

// Undo for the stream table (D11). An undo or redo is the inverse diff, saved through the profile's ProfileWriter like
// any other write (R5): there is no local state to roll back. Each builder takes the state from before the action and
// returns the entry; the entry's diffs are built from the rev current at their turn.
// Rules (opus I5), kept by the callers: an entry is pushed only after its write resolved OK; Undo and Redo are disabled
// while `writer.pending > 0` or an index-shifting write is out; an entry with `shifts` goes through onShift like the
// original action did.

export const HISTORY_MAX = 100
export const ENTRY_BUDGET = 8 * 1024 * 1024      // bytes of sidecar entries a delete's undo carries (the PUT limit is 16 MiB)
export const NOT_RESTORED = ' (editor documents not restored)'

export type UndoEntry = {
  label: string
  shifts: boolean
  undo: (rev: string) => StreamsDiff
  redo: (rev: string) => StreamsDiff
}

const range = (from: number, to: number) => Array.from({ length: Math.max(0, to - from) }, (_, i) => from + i)
const copyOf = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T
// The browser's measure of an entry (the backend counts the same compact JSON, re-review M6).
const sizeOf = (entry: SidecarEntry) => new TextEncoder().encode(JSON.stringify(entry)).length
const empty = (e: SidecarEntry | undefined) => !e || Object.keys(e).length === 0

export function undoToggle(index: number, before: boolean): UndoEntry {
  return {
    label: `${before ? 'disabling' : 'enabling'} stream ${index}`, shifts: false,
    undo: (rev) => toggleDiff(index, before, rev), redo: (rev) => toggleDiff(index, !before, rev),
  }
}

// Streams whose `next` is `name`, by index: what a rename to nothing clears, to be put back by the undo. `rows` is the
// page read before the action.
export function pointersTo(rows: StreamRow[], name: string | number, except: number): Record<number, string | number> {
  const out: Record<number, string | number> = {}
  for (const r of rows) if (r.index !== except && r.stream.next === name) out[r.index] = name
  return out
}

// A rename. The backend follows a rename in every `next`, so the name alone restores them; a rename to nothing clears
// the `next` that named it, which `pointers` (streams by index that named `oldName`) puts back.
export function undoRename(index: number, oldName: string | number | null, newName: string | number | null, pointers: Record<number, string | number> = {}): UndoEntry {
  return {
    label: `renaming stream ${index}`, shifts: false,
    undo: (rev) => {
      const d = renameDiff(index, oldName, rev)
      for (const [j, next] of Object.entries(pointers)) (d.changed as Record<string, StreamPatch>)[j] = { next }
      return d
    },
    redo: (rev) => renameDiff(index, newName, rev),
  }
}

// The patch that puts `before`'s values back: a key `before` lacks goes back to its default (null removes name, next and
// random_seed).
const DEFAULTS: Record<string, unknown> = { name: null, next: null, random_seed: null, start_paused: false, core_id: -1 }
function inverse(before: StreamJson, patch: StreamPatch): StreamPatch {
  const out: Record<string, unknown> = {}
  for (const k of Object.keys(patch)) {
    const had = (before as unknown as Record<string, unknown>)[k]
    out[k] = had === undefined ? DEFAULTS[k] ?? null : copyOf(had)
  }
  return out as StreamPatch
}

// A field-level patch of one stream (the properties form, the embedded editor's Save). `beforeSidecar` is the entry the
// write replaced and `opts.afterSidecar` the one it wrote: the undo and the redo put each back. `opts.pointers` is as for
// undoRename.
export function undoPatch(index: number, before: StreamJson, patch: StreamPatch, beforeSidecar?: SidecarEntry,
  opts: { afterSidecar?: SidecarEntry; pointers?: Record<number, string | number> } = {}): UndoEntry {
  const side = (e: SidecarEntry | undefined) => (e === undefined ? {} : { sidecar: { [String(index)]: copyOf(e) } })
  return {
    label: `editing stream ${index}`, shifts: false,
    undo: (rev) => {
      const changed: Record<string, StreamPatch> = { [String(index)]: inverse(before, patch) }
      for (const [j, next] of Object.entries(opts.pointers ?? {})) changed[j] = { next }
      return { rev, changed, ...side(beforeSidecar) }
    },
    redo: (rev) => ({ rev, changed: { [String(index)]: copyOf(patch) }, ...side(opts.afterSidecar) }),
  }
}

// A delete of `rows` (the deleted rows as shown before, any order). `entries` are their sidecar entries by index, as far
// as fetched; `cleared` the surviving streams whose `next` the delete cleared (by old index, with the name they went
// to); `countAfter` the stream count after the delete. The undo adds the rows back at their old indices. Entries over
// ENTRY_BUDGET in total, or missing for a row that has an editor document, are not restored, and the label says so.
export function undoDelete(rows: StreamRow[], entries: Record<number, SidecarEntry>, cleared: Record<number, string | number>, countAfter: number): UndoEntry {
  const gone = [...rows].sort((a, b) => a.index - b.index)
  const within = gone.reduce((n, r) => n + sizeOf(entries[r.index] ?? {}), 0) <= ENTRY_BUDGET
  const missing = gone.some((r) => r.has_editor && empty(entries[r.index]))
  const kept = gone.map((r) => (within && !empty(entries[r.index]) ? copyOf(entries[r.index]) : {}))
  const lost = !within || missing
  const total = countAfter + gone.length
  const goneAt = new Map(gone.map((r, k) => [r.index, k]))
  const order: (number | string)[] = []
  let survivor = 0
  for (let pos = 0; pos < total; pos++) order.push(goneAt.has(pos) ? `+${goneAt.get(pos)}` : survivor++)
  // a surviving stream's index after the delete: its old index less the deleted ones before it
  const after = (old: number) => old - gone.filter((r) => r.index < old).length
  return {
    label: `deleting ${gone.length === 1 ? 'a stream' : `${gone.length} streams`}${lost ? NOT_RESTORED : ''}`, shifts: true,
    undo: (rev) => {
      const changed: Record<string, StreamPatch> = {}
      for (const [old, next] of Object.entries(cleared)) changed[String(after(Number(old)))] = { next }
      return {
        rev, added: gone.map((r) => copyOf(r.stream)), order, ...(kept.some((e) => !empty(e)) ? { added_sidecar: copyOf(kept) } : {}),
        ...(Object.keys(changed).length ? { changed } : {}),
      }
    },
    redo: (rev) => deleteDiff(gone.map((r) => r.index), rev),
  }
}

// Streams added (a paste, a duplicate, Add stream): `countBefore` streams were there, `added` landed from index `at`
// on (the end by default), with `entries` their sidecar entries. The undo removes exactly those rows.
export function undoAdd(countBefore: number, added: StreamJson[], at: number = countBefore, entries?: SidecarEntry[]): UndoEntry {
  return {
    label: added.length === 1 ? 'adding a stream' : `adding ${added.length} streams`, shifts: true,
    undo: (rev) => deleteDiff(range(at, at + added.length), rev),
    redo: (rev) => pasteDiff(countBefore, at >= countBefore ? null : at - 1, added, rev, entries),
  }
}

// A reorder by `order` (old indices in their new places): the undo is the inverse permutation.
export function undoReorder(order: number[]): UndoEntry {
  const back: number[] = []
  order.forEach((old, pos) => { back[old] = pos })
  return {
    label: 'moving streams', shifts: true,
    undo: (rev) => ({ rev, order: [...back] }), redo: (rev) => ({ rev, order: [...order] }),
  }
}

// The sidecar entries of `rows` that have an editor document, by index, fetched one by one and stopping once their
// total passes `budget` (the caller then sees the total is over). A failed fetch leaves its row out.
export async function fetchEntries(slug: string, rows: StreamRow[], budget: number = ENTRY_BUDGET): Promise<Record<number, SidecarEntry>> {
  const out: Record<number, SidecarEntry> = {}
  let total = 0
  for (const r of rows) {
    if (!r.has_editor) continue
    if (total > budget) break
    try {
      const { entry } = await streamSidecar(slug, r.index)
      out[r.index] = entry
      total += sizeOf(entry)
    } catch { /* the row's document is then not restored or copied */ }
  }
  return out
}

// The undo and redo stacks of one profile editor (cap HISTORY_MAX). A new action empties the redo stack. `version`
// changes with every change, for useSyncExternalStore.
export class UndoHistory {
  private done: UndoEntry[] = []
  private undone: UndoEntry[] = []
  private listeners = new Set<() => void>()
  version = 0

  subscribe = (fn: () => void): (() => void) => { this.listeners.add(fn); return () => { this.listeners.delete(fn) } }
  getVersion = (): number => this.version
  private changed() { this.version += 1; this.listeners.forEach((fn) => fn()) }

  push(e: UndoEntry): void {
    this.done.push(e)
    if (this.done.length > HISTORY_MAX) this.done.shift()
    this.undone = []
    this.changed()
  }
  clear(): void {
    if (this.done.length === 0 && this.undone.length === 0) return
    this.done = []; this.undone = []
    this.changed()
  }
  nextUndo(): UndoEntry | undefined { return this.done.at(-1) }
  nextRedo(): UndoEntry | undefined { return this.undone.at(-1) }
  // The write of `nextUndo()` landed: it is now the one to redo.
  undid(): void { const e = this.done.pop(); if (e) { this.undone.push(e); this.changed() } }
  // The write of `nextRedo()` landed.
  redid(): void { const e = this.undone.pop(); if (e) { this.done.push(e); this.changed() } }
}
