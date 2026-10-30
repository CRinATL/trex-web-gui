import { ApiError, putStreams, type DiffReply, type StreamsDiff } from '../../api'

// 'stale': another writer changed the profile (409 stale); 'gone': it was renamed or deleted (404). Either ends the writer.
export type WriterState = 'ready' | 'stale' | 'gone'

// Which refusal ends a writer: a 404 (the profile is gone) or a 409 stale.
export const endOf = (x: unknown): WriterState | null =>
  x instanceof ApiError ? (x.status === 404 ? 'gone' : x.status === 409 && x.code === 'stale' ? 'stale' : null) : null

// One editor's writes to one profile, in order: each diff is built from the previous reply's rev when its turn comes,
// so two quick clicks never send one rev twice. A stale or gone reply rejects that write and every later one with the
// same ApiError (opus M7); any other refusal rejects only its own write.
export class ProfileWriter {
  readonly slug: string
  rev: string
  // The stream count of the last reply, null before the first: exact at a queued write's turn, when every write before it has landed.
  count: number | null = null
  // Writes queued or in flight. Undo and Redo wait for it to reach 0 (opus I5).
  pending = 0
  private pendingListeners = new Set<() => void>()
  private state: WriterState = 'ready'
  private ended: ApiError | null = null
  private tail: Promise<unknown> = Promise.resolve()
  private listeners = new Set<() => void>()

  constructor(slug: string, rev: string) { this.slug = slug; this.rev = rev }

  getState = (): WriterState => this.state
  // For useSyncExternalStore: `fn` runs when the state changes.
  subscribe = (fn: () => void): (() => void) => { this.listeners.add(fn); return () => { this.listeners.delete(fn) } }

  // For useSyncExternalStore over `pending`: `fn` runs whenever it changes (not on a state change, so `subscribe`'s
  // listeners hear state changes only).
  subscribePending = (fn: () => void): (() => void) => { this.pendingListeners.add(fn); return () => { this.pendingListeners.delete(fn) } }

  write(build: (rev: string) => StreamsDiff): Promise<DiffReply> {
    return this.run((rev) => putStreams(this.slug, build(rev)))
  }

  // Any request that writes the profile under a rev and answers a DiffReply (a PUT of a diff, or the pcap append's
  // import), queued with the others: `send` gets the rev when its turn comes. `ends` says which refusal ends the writer.
  run(send: (rev: string) => Promise<DiffReply>, ends: (x: unknown) => WriterState | null = endOf): Promise<DiffReply> {
    const run = async (): Promise<DiffReply> => {
      try {
        if (this.ended) throw this.ended
        const r = await send(this.rev)
        this.rev = r.rev
        this.count = r.stream_count
        return r
      } catch (x) {
        if (x === this.ended) throw x          // refused because the writer had ended: nothing new to report
        const end = ends(x)
        if (end) { this.ended = x as ApiError; this.state = end; this.listeners.forEach((fn) => fn()) }
        throw x
      } finally {
        this.pending -= 1
        this.pendingListeners.forEach((fn) => fn())
      }
    }
    this.pending += 1
    this.pendingListeners.forEach((fn) => fn())
    const p = this.tail.then(run, run)
    this.tail = p.catch(() => undefined)
    return p
  }
}
