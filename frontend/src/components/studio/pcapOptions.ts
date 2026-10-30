import type { PcapOptions } from '../../api'
import { NAME_RE } from './streamForm'

// What a new pcap import starts from: the pcap's own timing at ×1, one loop, no prefix, no address rewrite.
export const PCAP_DEFAULTS: PcapOptions = { ipg_usec: null, speedup: 1, loop_count: 1, name_prefix: '', rewrite_src: null, rewrite_dst: null }

const U32 = 2 ** 32 - 1
const NAME_PREFIX_MAX = 40
// A dotted IPv4 address as Python's ipaddress.IPv4Address reads it: four 0-255 parts, no leading zeros.
const IPV4_RE = /^(0|[1-9]\d{0,2})\.(0|[1-9]\d{0,2})\.(0|[1-9]\d{0,2})\.(0|[1-9]\d{0,2})$/
function ipv4(text: string): number | null {
  const m = IPV4_RE.exec(text)     // untrimmed, as the backend reads it
  if (!m) return null
  const parts = m.slice(1).map(Number)
  return parts.every((p) => p <= 255) ? parts.reduce((a, p) => a * 256 + p, 0) : null
}

// The first problem with `o`, in the words of the form, or null. It mirrors check_pcap_options
// (backend/trex_web_gui/trexapi/profiles.py:116 "def check_pcap_options"); the backend is the authority.
export function pcapError(o: PcapOptions): string | null {
  if (!(Number.isFinite(o.speedup) && o.speedup > 0)) return 'Speedup: a number above 0.'
  // load_pcap refuses a gap below 0.001 µs after the speedup, which a fixed gap leaves at 1
  if (o.ipg_usec !== null && !(Number.isFinite(o.ipg_usec) && o.ipg_usec >= 0.001 * o.speedup)) return 'Fixed gap: at least 0.001 µs.'
  if (!(Number.isInteger(o.loop_count) && o.loop_count >= 0 && o.loop_count <= 65535)) return 'Loops: a whole number 0-65535 (0 loops forever).'
  if (o.name_prefix !== '' && !(o.name_prefix.length <= NAME_PREFIX_MAX && NAME_RE.test(o.name_prefix)))
    return `Name prefix: up to ${NAME_PREFIX_MAX} printable ASCII characters, without quotes or backslash.`
  for (const [what, rw] of [['Rewrite source', o.rewrite_src], ['Rewrite destination', o.rewrite_dst]] as const) {
    if (rw === null) continue
    const start = ipv4(rw.start)
    if (start === null) return `${what}: the start is a dotted IPv4 address.`
    if (!(Number.isInteger(rw.count) && rw.count >= 1)) return `${what}: the count is a whole number of at least 1.`
    if (start + rw.count - 1 > U32) return `${what}: ${rw.start} + ${rw.count} addresses pass 255.255.255.255.`
  }
  return null
}
