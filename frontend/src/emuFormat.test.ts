import { describe, expect, it } from 'vitest'
import type { EmuClient } from './api'
import { dgText, ipv6Text, nsId, nsLabel } from './emuFormat'

describe('emuFormat', () => {
  it('formats IPv6 compressed', () => {
    const z = new Array(16).fill(0)
    expect(ipv6Text(z)).toBe('::')
    expect(ipv6Text([...z.slice(0, 15), 1])).toBe('::1')
    expect(ipv6Text([0xfe, 0x80, ...z.slice(2, 15), 1])).toBe('fe80::1')
    expect(ipv6Text([0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 1])).toBe('1:0:1::1:0:1')
    expect(ipv6Text([0, 1, 0, 0, 0, 1, 0, 2, 0, 3, 0, 4, 0, 5, 0, 6])).toBe('1:0:1:2:3:4:5:6')
  })
  it('says the gateway resolution', () => {
    expect(dgText({ dgw: { resolve: false, rmac: [0, 0, 0, 0, 0, 0] } } as EmuClient)).toBe('unresolved')
    expect(dgText({ dgw: { resolve: true, rmac: [1, 2, 3, 4, 5, 0xff] } } as EmuClient)).toBe('resolved 01:02:03:04:05:ff')
  })
  it('keys a namespace by port and tags, and labels it by the tags that are set', () => {
    expect(nsId({ vport: 1, tci: [10, 20], tpid: [0, 0] })).not.toBe(nsId({ vport: 1, tci: [0, 0], tpid: [0, 0] }))
    expect(nsLabel({ vport: 1, tci: [0, 0], tpid: [0, 0] })).toBe('port 1')
    expect(nsLabel({ vport: 2 })).toBe('port 2')
    expect(nsLabel({ vport: 1, tci: [10, 20], tpid: [0x81, 0] })).toBe('port 1, VLAN 10/20, tpid 129/0')
  })
})
