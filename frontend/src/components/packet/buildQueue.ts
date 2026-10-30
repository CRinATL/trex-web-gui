import type { BuildResult, PacketDoc } from '../../api'

// One build in flight per editor; every submit takes the next sequence number. A reply whose number is below the latest
// submitted is dropped (the caller gets 'superseded'), and docs that wait while a build runs collapse to the newest.
// A failure of a dropped request is dropped with it: the newer document is built next, once, which is also what a 503
// engine_restarted on a superseded request needs (the worker died on an older document, so the current one is safe to send).
export class BuildQueue {
  private seq = 0
  private busy = false
  private waiting: { seq: number; doc: PacketDoc; resolve: (r: BuildResult | 'superseded') => void; reject: (e: unknown) => void } | null = null
  private readonly send: (doc: PacketDoc) => Promise<BuildResult>

  constructor(send: (doc: PacketDoc) => Promise<BuildResult>) { this.send = send }

  // The result for `doc`, 'superseded' when a newer doc was submitted first, or the build's own rejection when `doc` is the newest.
  submit(doc: PacketDoc): Promise<BuildResult | 'superseded'> {
    return new Promise((resolve, reject) => {
      const seq = ++this.seq
      this.waiting?.resolve('superseded')
      this.waiting = { seq, doc, resolve, reject }
      if (!this.busy) this.next()
    })
  }

  private next(): void {
    const job = this.waiting
    if (job === null) return
    this.waiting = null
    this.busy = true
    // a send that throws synchronously is a rejection, not a stuck queue
    let sent: Promise<BuildResult>
    try { sent = this.send(job.doc) } catch (e) { sent = Promise.reject(e) }
    sent.then(
      (r) => { this.busy = false; if (job.seq < this.seq) job.resolve('superseded'); else job.resolve(r); this.next() },
      (e) => { this.busy = false; if (job.seq < this.seq) job.resolve('superseded'); else job.reject(e); this.next() },
    )
  }
}
