import { describe, expect, it, vi } from 'vitest'
import type { PacketTree } from '../../api'
import { decodeBase64, encodeBase64, layerEnds, rowAt, rangeOfField, rangeText, rowsOf } from './hexModel'

describe('hexModel', () => {
  it('decodeBase64 gives the bytes', () => {
    expect(Array.from(decodeBase64('AAEC/w=='))).toEqual([0, 1, 2, 255])
  })
  it('rowsOf a 20-byte frame gives two rows', () => {
    const rows = rowsOf(new Uint8Array(20))
    expect(rows.map((r) => [r.offset, r.bytes.length])).toEqual([[0, 16], [16, 4]])
  })
  it('non-printable bytes become dots', () => {
    expect(rowsOf(Uint8Array.from([0x41, 0x00, 0x7f, 0x20, 0x7e, 0x1f]))[0].ascii).toBe('A.. ~.')
  })
  it('a field range is its layer offset plus its own, in bytes; length 0 has none', () => {
    expect(rangeOfField({ offset: 1, length: 1 }, 14)).toEqual([15, 16])
    expect(rangeOfField({ offset: 6, length: 6 })).toEqual([6, 12])
    expect(rangeOfField({ offset: 0, length: 0 }, 14)).toBeNull()
  })
  it('rangeText is %04x-%04x [%04d]', () => {
    expect(rangeText([14, 34])).toBe('000e-0021 [0020]')
  })
  it('encodeBase64 round-trips, a large frame too', () => {
    const big = Uint8Array.from({ length: 70000 }, (_, i) => (i * 7) & 0xff)
    expect(Array.from(decodeBase64(encodeBase64(big)))).toEqual(Array.from(big))
    expect(encodeBase64(Uint8Array.from([0, 1, 2, 255]))).toBe('AAEC/w==')
  })
  it('layerEnds computes once per binary', () => {
    const tree = (binary: string, offsets: number[]): PacketTree => ({ binary, data: offsets.map((offset, i) => ({ id: `L${i}`, offset, real_id: `L${i}`, valid_structure: true, fields: [] })) })
    const atob = vi.spyOn(globalThis, 'atob')
    try {
      const a = tree('AAECAwQFBgcICQ==', [0, 4, 8])   // 10 bytes
      expect(layerEnds(a)).toEqual([4, 8, 10])
      expect(layerEnds(a)).toEqual([4, 8, 10])
      expect(layerEnds({ ...a })).toEqual([4, 8, 10])   // another tree object of the same frame
      expect(atob).toHaveBeenCalledTimes(1)
      expect(layerEnds(tree('AAEC', [0, 1]))).toEqual([1, 3])   // another frame
      expect(atob).toHaveBeenCalledTimes(2)
      expect(layerEnds(tree('AAEC', [0, 2]))).toEqual([2, 3])   // the same frame with other layers is not a hit
    } finally { atob.mockRestore() }
  })
  it('rowAt gives one row with its offset and ASCII', () => {
    const bytes = Uint8Array.from({ length: 20 }, (_, i) => 0x41 + i)
    expect(rowAt(bytes, 1)).toEqual({ offset: 16, bytes: [0x51, 0x52, 0x53, 0x54], ascii: 'QRST' })
  })
})
