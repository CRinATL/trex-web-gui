import { describe, expect, it } from 'vitest'
import type { DecodedRow } from './api'
import { toPcap } from './pcapWriter'
import capturePkts from './__fixtures__/capturePkts.json'

const base = capturePkts.batch.rows[0] as DecodedRow
const b64 = (bytes: number[]) => btoa(String.fromCharCode(...bytes))
const row = (over: Partial<DecodedRow>): DecodedRow => ({ ...base, ...over })
const bytes = async (b: Blob) => new Uint8Array(await b.arrayBuffer())

describe('toPcap', () => {
  it('a two-row blob has the 24-byte header, two 16-byte records and the payloads in order', async () => {
    const a = [1, 2, 3], c = [9, 8, 7, 6, 5]
    const out = await bytes(toPcap([row({ binary: b64(a), wirelen: 64 }), row({ binary: b64(c), wirelen: 5 })]))
    const v = new DataView(out.buffer)
    expect(out.length).toBe(24 + 16 + 3 + 16 + 5)
    expect(v.getUint32(0, true)).toBe(0xa1b2c3d4)
    expect([v.getUint16(4, true), v.getUint16(6, true)]).toEqual([2, 4])
    expect(v.getUint32(16, true)).toBe(262144)    // snaplen, as the backend spool writes it
    expect(v.getUint32(20, true)).toBe(1)         // Ethernet
    expect([v.getUint32(32, true), v.getUint32(36, true)]).toEqual([3, 64])   // incl_len, orig_len
    expect(Array.from(out.slice(40, 43))).toEqual(a)
    expect([v.getUint32(51, true), v.getUint32(55, true)]).toEqual([5, 5])
    expect(Array.from(out.slice(59))).toEqual(c)
  })

  it('ts_rel_us 1500000 splits into 1 s and 500000 µs', async () => {
    const out = await bytes(toPcap([row({ ts_rel_us: 1500000, binary: b64([0]) })]))
    const v = new DataView(out.buffer)
    expect([v.getUint32(24, true), v.getUint32(28, true)]).toEqual([1, 500000])
  })

  it('no rows is a header alone', async () => {
    expect((await bytes(toPcap([]))).length).toBe(24)
  })

  it('a negative ts_rel_us is clamped to zero rather than wrapping', async () => {
    const out = await bytes(toPcap([row({ binary: b64([1]), ts_rel_us: -1_500_000 })]))
    const v = new DataView(out.buffer)
    expect([v.getUint32(24, true), v.getUint32(28, true)]).toEqual([0, 0])
  })
})

describe('toPcap input', () => {
  it('takes bare binary, ts_rel_us and wirelen records, not whole decoded rows', async () => {
    const out = await bytes(toPcap([{ binary: b64([7, 7]), ts_rel_us: 0, wirelen: 2 }]))
    expect(out.length).toBe(24 + 16 + 2)
  })
})
