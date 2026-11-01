import { describe, expect, it, vi } from 'vitest'
import type { PacketTree } from '../../api'
import { decodeBase64, encodeBase64, fieldRange, layerEnds, rowAt, rangeOfField, rangeText, rowsOf } from './hexModel'

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
  it('a field ends where the next one starts, the last at the layer\'s end: the service reports a variable-size field by its nominal size', () => {
    const f = (offset: number, length: number) => ({ offset, length })
    // measured (tests/golden/trp/ARP__ARP-REQUEST.json): ARP at 14, op 6, hwsrc 8, psrc 14, hwdst 18, pdst 24, each reported as 2 bytes
    const arp = [f(6, 2), f(8, 2), f(14, 2), f(18, 2), f(24, 2)]
    expect(arp.map((_, k) => fieldRange(arp, k, 14, 42))).toEqual([[20, 22], [22, 28], [28, 32], [32, 38], [38, 42]])
    // measured (tests/golden/trp/DHCP__DHCP-DISCOVER.json): BOOTP options at 236 reported 2 bytes, 4 in the frame
    expect(fieldRange([f(108, 128), f(236, 2)], 1, 42, 282)).toEqual([278, 282])
    expect(fieldRange([f(0, 2)], 0, 282, 292)).toEqual([282, 292])                           // DHCP options: 10 bytes
    expect(fieldRange([f(16, 4), f(20, 2)], 1, 14, 34)).toBeNull()                          // empty IP options: no bytes
    // IP id, then flags and frag (bit fields, reported at 6 with length 0), then ttl, cut short here so ttl is last
    const ip = [f(4, 2), f(6, 0), f(6, 0), f(8, 1)]
    expect(ip.map((_, k) => fieldRange(ip, k, 14, 34))).toEqual([[18, 20], null, null, [22, 34]])
    // measured through the service (fable review, 2026-11-01): IPv6 hop-by-hop at 54: nh 0, len 1, autopad 2 (adds no
    // bytes, reported 1), options 2 (6 bytes, reported 2); DNS with qd and ar: an and ns empty, both reported at ar's 29
    const hbh = [f(0, 1), f(1, 1), f(2, 1), f(2, 2)]
    expect(hbh.map((_, k) => fieldRange(hbh, k, 54, 62))).toEqual([[54, 55], [55, 56], null, [56, 62]])
    const dns = [f(12, 2), f(29, 2), f(29, 2), f(29, 2)]
    expect(dns.map((_, k) => fieldRange(dns, k, 42, 82))).toEqual([[54, 71], null, null, [71, 82]])
    expect(fieldRange([f(4, 2), f(2, 2)], 0, 0, 10)).toEqual([4, 6])                         // offsets going backwards: the reported size
    expect(fieldRange([f(2, 4)], 0, 0, 4)).toEqual([2, 4])                                   // never past the layer
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
