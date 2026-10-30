import type { DecodedRow } from './api'

const MAGIC = 0xa1b2c3d4
const SNAPLEN = 262144       // as backend/trex_web_gui/store/spool.py writes it
const ETHERNET = 1

// A classic pcap (little endian, microsecond timestamps) of the rows, each stamped with its time since the capture started.
export function toPcap(rows: Pick<DecodedRow, 'binary' | 'ts_rel_us' | 'wirelen'>[]): Blob {
  const header = new DataView(new ArrayBuffer(24))
  header.setUint32(0, MAGIC, true)
  header.setUint16(4, 2, true)
  header.setUint16(6, 4, true)
  header.setUint32(16, SNAPLEN, true)
  header.setUint32(20, ETHERNET, true)
  const parts: BlobPart[] = [header.buffer]
  for (const r of rows) {
    const raw = Uint8Array.from(atob(r.binary), (c) => c.charCodeAt(0))
    const rec = new DataView(new ArrayBuffer(16))
    const us = Math.max(0, r.ts_rel_us)   // a negative offset would wrap in setUint32; the spool writer clamps too
    rec.setUint32(0, Math.floor(us / 1_000_000), true)
    rec.setUint32(4, us % 1_000_000, true)
    rec.setUint32(8, raw.length, true)
    rec.setUint32(12, r.wirelen, true)
    parts.push(rec.buffer, raw)
  }
  return new Blob(parts, { type: 'application/vnd.tcpdump.pcap' })
}
