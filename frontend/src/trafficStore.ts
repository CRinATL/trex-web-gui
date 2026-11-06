import type { Inspected, LibraryEntry } from './api'
import type { MultUnit } from './traffic'

// One port's Traffic form. Browser-side and outside the reducer (the captureStore pattern), so the form survives
// switching ports (TrafficTab is keyed by port) and saved setups can read and write it. The pending live-update
// countdown lives here too, so the toolbar can drop it; confirm, busy, error, the assigned table and rateInfo stay in the component.
export interface TrafficForm {
  path: string
  inspected: Inspected | null
  values: Record<string, unknown>
  extra: string
  specs: Inspected['tunables']     // the last good specs for formPath
  formPath: string | null          // the path the form values belong to
  multValue: string
  unit: MultUnit
  applied: { value: string; unit: MultUnit }   // the last multiplier the server accepted
  duration: string
  force: boolean
  pending: { mult: string; left: number } | null   // the live-update countdown (A3.3); null when none is running
  libraryArgs: string[]            // that entry's stored argv: the first Inspect of the picked .py runs with it, before the tunable specs are known
  libraryPy: string | null         // the .py source path a library pick filled in; the Traffic tab says beside Assign what that assigns
  librarySlug: string | null       // the slug of the entry that pick came from; requests send it only through linkedSlug, which checks the form still matches the pick
}

export const DEFAULT_FORM: TrafficForm = Object.freeze({
  path: '', inspected: null, values: Object.freeze({}), extra: '', specs: Object.freeze([]) as unknown as TrafficForm['specs'], formPath: null,
  multValue: '1', unit: 'x', applied: Object.freeze({ value: '1', unit: 'x' as MultUnit }), duration: '', force: false, pending: null, libraryPy: null, librarySlug: null, libraryArgs: Object.freeze([]) as unknown as string[],
})

class TrafficStore {
  private forms: Record<number, TrafficForm> = {}
  private gen = 0
  private listeners = new Set<() => void>()

  private publish() { this.listeners.forEach((fn) => fn()) }

  // The same object until that port's next set, so useSyncExternalStore does not loop.
  get = (port: number): TrafficForm => this.forms[port] ?? DEFAULT_FORM

  // A new object for this port only; the others keep their identity.
  set = (port: number, patch: Partial<TrafficForm>) => {
    this.forms = { ...this.forms, [port]: { ...this.get(port), ...patch } }
    this.publish()
  }

  // A library rename: forms on the old file path move to the entry's path, forms linked to the old slug take the new one,
  // and both show the entry's name. Other forms keep their identity.
  relink = (old: { slug: string; path: string }, entry: LibraryEntry): void => {
    let changed = false
    const next: Record<number, TrafficForm> = {}
    for (const [k, f] of Object.entries(this.forms)) {
      const onPath = f.path === old.path || f.formPath === old.path || f.inspected?.path === old.path
      const onSlug = f.librarySlug === old.slug
      if (!onPath && !onSlug) { next[Number(k)] = f; continue }
      const patch: Partial<TrafficForm> = {}
      if (onPath) {
        if (f.path === old.path) patch.path = entry.path
        if (f.formPath === old.path) patch.formPath = entry.path
      }
      if (onSlug) patch.librarySlug = entry.slug
      if (f.inspected) patch.inspected = { ...f.inspected, name: entry.name, ...(f.inspected.path === old.path ? { path: entry.path } : {}) }
      next[Number(k)] = { ...f, ...patch }
      changed = true
    }
    if (!changed) return
    this.forms = next
    this.publish()
  }

  // Every port that has been touched, keyed by port id. A new record on every change.
  all = (): Record<number, TrafficForm> => this.forms

  // A connect_seq change (state.tsx) and the test hook.
  reset = () => { this.forms = {}; this.gen++; this.publish() }

  // Counts resets. An async completion captures it before it awaits and writes only if it is unchanged, so a reply
  // that lands after a connect_seq reset cannot write a partial form into the cleared store.
  generation = (): number => this.gen

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn)
    return () => { this.listeners.delete(fn) }
  }
}

export const trafficStore = new TrafficStore()
