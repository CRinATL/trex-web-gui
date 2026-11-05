import { macText, type EmuClient, type EmuNsKey } from './api'

export const CLIENTS_PAGE = 100

// The namespace named for a person: its port, then its VLAN tags and tag types when any is set (two namespaces may share a port).
export function nsLabel(k: EmuNsKey): string {
  const set = (v?: readonly number[]) => (v ?? []).some((x) => x !== 0)
  return `port ${k.vport}${set(k.tci) ? `, VLAN ${k.tci!.join('/')}` : ''}${set(k.tpid) ? `, tpid ${k.tpid!.join('/')}` : ''}`
}
// A namespace's identity on the wire: the port and the two VLAN tag lists.
export const nsId = (k: EmuNsKey) => `${k.vport}/${(k.tci ?? []).join(',')}/${(k.tpid ?? []).join(',')}`
// Four IPv4 bytes as dotted text.
export const dotted = (b: readonly number[]) => b.join('.')

// 16 bytes as RFC 5952 text: groups of 16 bits in lower-case hex, the longest run (two or more) of zero groups as `::`.
export function ipv6Text(b: readonly number[]): string {
  const g = Array.from({ length: 8 }, (_, i) => ((b[2 * i] << 8) | b[2 * i + 1]).toString(16))
  let best = -1, bestLen = 0
  for (let i = 0; i < 8;) {
    if (g[i] !== '0') { i++; continue }
    let j = i
    while (j < 8 && g[j] === '0') j++
    if (j - i > bestLen) { best = i; bestLen = j - i }
    i = j
  }
  if (bestLen < 2) return g.join(':')
  return `${g.slice(0, best).join(':')}::${g.slice(best + bestLen).join(':')}`
}

// The IPv6 fields a client has set, labelled; all-zero ones are left out.
export function v6Lines(c: EmuClient): string[] {
  const f: [string, readonly number[] | null][] = [['addr', c.ipv6], ['local', c.ipv6_local], ['slaac', c.ipv6_slaac], ['dhcp', c.dhcp_ipv6], ['dg', c.dg_ipv6]]
  return f.filter(([, v]) => v !== null && v.some((x) => x !== 0)).map(([k, v]) => `${k} ${ipv6Text(v!)}`)
}
// "resolved <rmac>" once the default gateway's MAC is known, else "unresolved".
export const dgText = (c: EmuClient) => (c.dgw.resolve ? `resolved ${macText(c.dgw.rmac)}` : 'unresolved')
