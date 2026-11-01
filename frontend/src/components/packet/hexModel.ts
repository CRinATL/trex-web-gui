import type { PacketField, PacketTree } from '../../api'

export type Range = [start: number, end: number]   // byte indexes into the frame, end exclusive
export interface HexRow { offset: number; bytes: number[]; ascii: string }

export function decodeBase64(b64: string): Uint8Array {
  const bin = atob(b64)
  return Uint8Array.from(bin, (c) => c.charCodeAt(0))
}

export const toHex = (bytes: Uint8Array) => Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join(' ')

// Row `n` of the frame: 16 bytes, and their ASCII (a byte outside 0x20..0x7e shows as a dot).
export function rowAt(bytes: Uint8Array, n: number): HexRow {
  const offset = n * 16
  const chunk = Array.from(bytes.subarray(offset, offset + 16))
  return { offset, bytes: chunk, ascii: chunk.map((b) => (b >= 0x20 && b <= 0x7e ? String.fromCharCode(b) : '.')).join('') }
}

export const rowsOf = (bytes: Uint8Array): HexRow[] => Array.from({ length: Math.ceil(bytes.length / 16) }, (_, n) => rowAt(bytes, n))

// The service reports byte offsets (measured: tests/fixtures/p3c/trees.json, IP `tos` is offset 1, length 1, in a layer at 14),
// and a field's offset is relative to its layer. A field of length 0 (a bit field such as IP version) has no byte range.
export function rangeOfField(field: Pick<PacketField, 'offset' | 'length'>, layerOffset = 0): Range | null {
  if (!(field.length > 0)) return null
  const start = layerOffset + field.offset
  return [start, start + field.length]
}

// A field's bytes within its layer, which ends at `end`: none past the end. The layer's `last` field runs to the end,
// because the service reports a variable-size field (IP, TCP, BOOTP and DHCP options; ARP's addresses) by its nominal
// size, not its built one (vendored scapy_service.py:701-706); a fixed-size last field ends there anyway. Only the last
// field is corrected: ARP's hwsrc, psrc and hwdst keep the nominal 2 bytes.
export function fieldRange(field: Pick<PacketField, 'offset' | 'length'>, layerOffset: number, end: number, last: boolean): Range | null {
  const r = rangeOfField(field, layerOffset)
  if (r === null || r[0] >= end) return null
  return [r[0], last ? end : Math.min(r[1], end)]
}

// The text of a range as the tree shows it: first and last byte in hex, then the length (B2.9).
export const rangeText = ([start, end]: Range) =>
  `${start.toString(16).padStart(4, '0')}-${(end - 1).toString(16).padStart(4, '0')} [${String(end - start).padStart(4, '0')}]`

export function encodeBase64(bytes: Uint8Array): string {
  let bin = ''
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return btoa(bin)
}

// Where each layer's bytes end: at the next layer, or at the end of the frame for the last one. Computed once per frame
// (the last one asked about), because every field of every layer asks (p3c-capture-112).
let ends: { binary: string; starts: string; value: number[] } | null = null
export function layerEnds(tree: PacketTree): number[] {
  const starts = tree.data.map((l) => l.offset).join(',')
  if (ends?.binary === tree.binary && ends.starts === starts) return ends.value
  const total = decodeBase64(tree.binary).length
  const value = tree.data.map((_, i) => (i + 1 < tree.data.length ? tree.data[i + 1].offset : total))
  ends = { binary: tree.binary, starts, value }
  return value
}
