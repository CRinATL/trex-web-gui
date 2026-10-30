import { describe, expect, it } from 'vitest'
import type { BuildResult, PacketDoc } from '../../api'
import { BuildQueue } from './buildQueue'

const doc = (caption: string): PacketDoc => ({ fileType: 'trex-packet-editor', version: '1.0.0', metadata: { caption }, packet: [{ id: 'Ether', fields: [] }], fePrarameters: {}, feInstructions: [] })
const result = (tag: string): BuildResult => ({ binary: tag, data: [], field_engine: null, error: null })

// A send that records its documents and lets the test settle each call when it chooses.
function manual() {
  const calls: { caption: string; resolve: (r: BuildResult) => void; reject: (e: unknown) => void }[] = []
  const send = (d: PacketDoc) => new Promise<BuildResult>((resolve, reject) => { calls.push({ caption: d.metadata.caption, resolve, reject }) })
  return { calls, send }
}

describe('BuildQueue', () => {
  it('an older build reply never replaces a newer one', async () => {
    const m = manual()
    const q = new BuildQueue(m.send)
    const a = q.submit(doc('A'))
    const b = q.submit(doc('B'))
    expect(m.calls.map((c) => c.caption)).toEqual(['A'])      // one in flight; B waits
    m.calls[0].resolve(result('A'))
    expect(await a).toBe('superseded')                        // A's reply is dropped, not shown
    await Promise.resolve()
    expect(m.calls.map((c) => c.caption)).toEqual(['A', 'B'])
    m.calls[1].resolve(result('B'))
    expect(await b).toEqual(result('B'))
  })

  it('queued docs collapse to the newest', async () => {
    const m = manual()
    const q = new BuildQueue(m.send)
    const first = q.submit(doc('A'))
    const rest = [q.submit(doc('B')), q.submit(doc('C')), q.submit(doc('D'))]
    m.calls[0].resolve(result('A'))
    await first
    await Promise.resolve()
    expect(m.calls.map((c) => c.caption)).toEqual(['A', 'D'])  // B and C were never sent
    m.calls[1].resolve(result('D'))
    expect(await Promise.all(rest)).toEqual(['superseded', 'superseded', result('D')])
  })

  it('a failure of the newest document is rejected; a failure of an older one is superseded', async () => {
    const m = manual()
    const q = new BuildQueue(m.send)
    const a = q.submit(doc('A'))
    const b = q.submit(doc('B'))
    m.calls[0].reject(new Error('A died'))
    expect(await a).toBe('superseded')
    await Promise.resolve()
    m.calls[1].reject(new Error('B died'))
    await expect(b).rejects.toThrow('B died')
    const c = q.submit(doc('C'))                              // the queue is free again
    expect(m.calls.map((x) => x.caption)).toEqual(['A', 'B', 'C'])
    m.calls[2].resolve(result('C'))
    expect(await c).toEqual(result('C'))
  })

  it('a send that throws is a rejection and does not stop the queue', async () => {
    let n = 0
    const q = new BuildQueue((d) => { if (n++ === 0) throw new Error('boom'); return Promise.resolve(result(d.metadata.caption)) })
    await expect(q.submit(doc('A'))).rejects.toThrow('boom')
    expect(await q.submit(doc('B'))).toEqual(result('B'))
  })
})
