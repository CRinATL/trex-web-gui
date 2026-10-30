import { useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import { ApiError, type BuildResult, type PacketDoc } from '../../api'
import { BuildQueue } from './buildQueue'
import { isAppend, placeOf, type EditorError, type Place } from './packetModel'
import { History } from './undo'

export const RESTART_TEXT = 'the packet engine restarted while evaluating this value'

// What one submitted document was: the edit's own details, kept until its reply arrives.
interface Job { doc: PacketDoc; before: PacketDoc | null; place: Place | null; append: boolean; label: string }

// A Field Engine pane edit that has no verdict yet: the document it replaced, and the parameter and text it set.
interface FeEdit { before: PacketDoc; key: string; value: string }
// The value a pane edit set that the Field Engine refused (B5.3), with the engine's message; the document went back.
export interface Rejected { key: string; value: string; message: string }

interface Snapshot {
  doc: PacketDoc
  result: BuildResult | null        // the newest reply that was not superseded (it may carry an error)
  lastGood: BuildResult | null      // the newest reply without a packet error: what the hex and the tree show
  error: EditorError | null
  rejected: Rejected | null
  pending: boolean                  // a build is in flight or waiting
  canUndo: boolean
  canRedo: boolean
}

export interface Editor extends Snapshot {
  // `fe` marks an edit made in the Field Engine pane: when the engine refuses it (or an earlier unbuilt one), the
  // document goes back to the last one that built, and the value is kept in `rejected`.
  apply(fn: (doc: PacketDoc) => PacketDoc, label: string, fe?: { key: string; value: string }): void
  undo(): void
  redo(): void
  reset(doc: PacketDoc): void
}

const hasField = (doc: PacketDoc, e: EditorError) =>
  e.layer !== null && e.field !== null && (doc.packet[e.layer]?.fields.some((f) => f.id === e.field) ?? false)

// The state behind useEditor, outside React so that a reply that arrives after an unmount or a reset touches nothing.
class Store {
  private snap: Snapshot
  private readonly listeners = new Set<() => void>()
  private readonly history = new History<PacketDoc>()
  private readonly queue: BuildQueue
  private build: (doc: PacketDoc) => Promise<BuildResult>
  private feEdits: FeEdit[] = []     // pane edits since the last document whose FE built, oldest first
  private outstanding = 0
  private live = false
  private started = false

  constructor(initial: PacketDoc, build: (doc: PacketDoc) => Promise<BuildResult>) {
    this.build = build
    this.queue = new BuildQueue((doc) => this.build(doc))
    this.snap = { doc: initial, result: null, lastGood: null, error: null, rejected: null, pending: false, canUndo: false, canRedo: false }
  }

  setBuild(build: (doc: PacketDoc) => Promise<BuildResult>): void { this.build = build }

  // Mount: build the initial document once (a second mount, as StrictMode makes, builds nothing more).
  start(): void {
    this.live = true
    if (this.started) return
    this.started = true
    this.run({ doc: this.snap.doc, before: null, place: null, append: false, label: 'open' })
  }

  stop(): void { this.live = false }

  subscribe = (fn: () => void): (() => void) => { this.listeners.add(fn); return () => { this.listeners.delete(fn) } }
  getSnapshot = (): Snapshot => this.snap

  apply = (fn: (doc: PacketDoc) => PacketDoc, label: string, fe?: { key: string; value: string }): void => {
    const before = this.snap.doc
    const doc = fn(before)
    if (doc === before) return
    this.history.push(before)
    // A pane edit joins the unbuilt ones; it starts a line only when the FE is not already failing (then it is a repair).
    if (fe !== undefined && (this.feEdits.length > 0 || this.snap.error?.kind !== 'fe')) this.feEdits.push({ before, ...fe })
    else this.feEdits = []
    this.set({ doc, rejected: null })
    this.run({ doc, before, place: placeOf(before, doc), append: isAppend(before, doc), label })
  }

  undo = (): void => this.step((cur) => this.history.undo(cur), 'undo')
  redo = (): void => this.step((cur) => this.history.redo(cur), 'redo')

  reset = (doc: PacketDoc): void => {
    this.history.clear()
    this.feEdits = []
    this.set({ doc, result: null, lastGood: null, error: null, rejected: null })
    this.run({ doc, before: null, place: null, append: false, label: 'open' })
  }

  private step(move: (current: PacketDoc) => PacketDoc | undefined, label: string): void {
    const from = this.snap.doc
    const doc = move(from)
    if (doc === undefined) return
    this.feEdits = []
    this.set({ doc, rejected: null })
    this.run({ doc, before: null, place: placeOf(from, doc), append: false, label })
  }

  private set(patch: Partial<Snapshot>): void {
    this.snap = { ...this.snap, ...patch, pending: this.outstanding > 0, canUndo: this.history.canUndo, canRedo: this.history.canRedo }
    this.listeners.forEach((fn) => fn())
  }

  private run(job: Job): void {
    this.outstanding++
    this.set({})
    this.queue.submit(job.doc).then(
      (r) => this.settle(job, r, null),
      (e: unknown) => this.settle(job, null, e),
    )
  }

  // A reply to `job`; 'superseded' replies and replies after the editor is gone change nothing but the pending flag.
  private settle(job: Job, r: BuildResult | 'superseded' | null, err: unknown): void {
    this.outstanding--
    if (!this.live || r === 'superseded') { if (this.live) this.set({}); return }
    if (r !== null) {
      if (r.error !== null) { this.feEdits = []; this.fail(job, 'build', r.error, r); return }
      const fe = r.field_engine?.error ?? null
      if (fe !== null && this.feEdits.length > 0 && job.doc === this.snap.doc) { this.revertFe(r, fe); return }
      this.feEdits = []
      this.set({ result: r, lastGood: r, error: fe === null ? null : { kind: 'fe', message: fe, layer: null, field: null, instruction: job.place?.instruction ?? null } })
      return
    }
    // A 503 engine_restarted on the editor's own latest document is a rejected commit: it is not sent again. (A restart on
    // an older document never reaches here: the queue answered 'superseded' and sent the current document next.)
    this.feEdits = []
    const restarted = err instanceof ApiError && err.status === 503 && err.code === 'engine_restarted'
    this.fail(job, restarted ? 'restart' : 'request', restarted ? RESTART_TEXT : err instanceof Error ? err.message : String(err), null)
  }

  // The Field Engine refused the newest pane edit (or one before it): take that edit back without leaving it redoable, and
  // build the document again. If that fails too, the next older pane edit goes, until the FE builds or none is left (B5.3).
  private revertFe(r: BuildResult, message: string): void {
    const edit = this.feEdits.pop()
    if (edit === undefined || !this.history.drop(edit.before)) { this.feEdits = []; this.set({ result: r, lastGood: r, error: { kind: 'fe', message, layer: null, field: null, instruction: null } }); return }
    this.set({ doc: edit.before, result: r, lastGood: r, error: null, rejected: { key: edit.key, value: edit.value, message } })
    this.run({ doc: edit.before, before: null, place: null, append: false, label: 'revert' })
  }

  // The edit was refused: keep lastGood, mark where, and undo a failed append.
  private fail(job: Job, kind: 'build' | 'restart' | 'request', message: string, r: BuildResult | null): void {
    if (job.append && job.before !== null) {
      this.history.drop(job.before)
      this.set({ doc: job.before, result: this.snap.lastGood, error: { kind: 'append', message: `${job.label} was undone: ${message}`, layer: null, field: null, instruction: null } })
      return
    }
    const prev = this.snap.error
    // The same failure as before, and the field it was on is still set: the new edit is elsewhere, so the mark stays.
    const keep = prev !== null && job.place?.field !== prev.field && prev.message === message && hasField(job.doc, prev)
    const at: Place = keep ? prev : (job.place ?? { layer: null, field: null, instruction: null })
    this.set({ ...(r !== null ? { result: r } : {}), error: { kind, message, layer: at.layer, field: at.field, instruction: at.instruction } })
  }
}

// The packet editor's state: the user's document, the newest build of it and the last good one, the error to show, and
// undo/redo. `build` is the request that builds a document (`buildPacket`); at most one is in flight.
export function useEditor(initial: PacketDoc, opts: { build: (doc: PacketDoc) => Promise<BuildResult> }): Editor {
  const [store] = useState(() => new Store(initial, opts.build))
  useEffect(() => { store.setBuild(opts.build) }, [store, opts.build])
  useEffect(() => { store.start(); return () => store.stop() }, [store])
  const snap = useSyncExternalStore(store.subscribe, store.getSnapshot)
  return useMemo(() => ({ ...snap, apply: store.apply, undo: store.undo, redo: store.redo, reset: store.reset }), [snap, store])
}
