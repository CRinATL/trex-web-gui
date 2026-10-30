import { describe, expect, it } from 'vitest'
import type { MonitorMsg } from './api'
import { clientServerMax, globalTiles, portRows, stfLatencyRows, templateRows } from './monitorLayouts'
import stl from './__fixtures__/monitorStl.json'
import stateful from './__fixtures__/monitorStateful.json'

const g = (stl as unknown as MonitorMsg).global
const tile = (l: ReturnType<typeof globalTiles>, id: string) => l.tiles.find((t) => t.id === id)

describe('monitorLayouts', () => {
  it('globalTiles reads the recorded trex-global', () => {
    const l = globalTiles(g)
    expect(tile(l, 'tx_bps')).toMatchObject({ group: 'Tx', kind: 'bps', value: 448.4 })
    expect(tile(l, 'tx_pps')?.value).toBe(0.8759)
    expect(tile(l, 'rx_bps')).toMatchObject({ group: 'Rx', value: 448.5 })
    expect(tile(l, 'rx_pps')?.value).toBe(0.876)
    expect(tile(l, 'tx_cps')?.value).toBe(0)
    expect(tile(l, 'active_flows')).toMatchObject({ group: 'Flows', value: 0 })
    expect(tile(l, 'open_flows')?.value).toBe(0)
    expect(tile(l, 'cpu_util')).toMatchObject({ group: 'System', kind: 'cpu', value: 0.05709 })
    expect(tile(l, 'rx_cpu_util')?.value).toBe(3.352e-06)
    expect(tile(l, 'total_tx_pkts')?.value).toBe(120712230)
    expect(tile(l, 'total_rx_bytes')?.value).toBe(7712642444)
    expect(['alloc_error', 'queue_full', 'queue_drop'].map((id) => tile(l, id)?.value)).toEqual([0, 0, 0])
    expect(l.expected.map((r) => [r.id, r.expected, r.measured])).toEqual([['bps', 0, 448.4], ['pps', 0, 0.8759], ['cps', 0, 0]])
    expect(l.tiles.some((t) => t.id.startsWith('nat_'))).toBe(false)   // every NAT counter is zero here
  })

  it('pct is null when nothing is expected', () => {
    expect(globalTiles(g).expected.every((r) => r.pct === null)).toBe(true)
    const l = globalTiles({ ...g, m_tx_expected_bps: 1000, m_tx_bps: 250 })
    expect(l.expected.find((r) => r.id === 'bps')?.pct).toBe(25)
  })

  it('flags a drop rate above 1 bps and not 1 bps itself', () => {
    expect(globalTiles({ ...g, m_rx_drop_bps: 1 }).dropFlagged).toBe(false)
    const l = globalTiles({ ...g, m_rx_drop_bps: 1.5 })
    expect(l.dropFlagged).toBe(true)
    expect(tile(l, 'rx_drop_bps')?.flagged).toBe(true)
  })

  it('shows a NAT counter only once it counts', () => {
    const l = globalTiles({ ...g, m_total_nat_open: 3 })
    expect(l.tiles.filter((t) => t.id.startsWith('nat_')).map((t) => [t.id, t.value])).toEqual([['nat_open', 3]])
  })

  it('reads a missing or null trex-global as empty readings', () => {
    expect(globalTiles(null).tiles.every((t) => t.value === null)).toBe(true)
    expect(portRows(null)).toEqual([])
  })

  it('portRows finds every port', () => {
    const rows = portRows(g)
    expect(rows.map((r) => r.port)).toEqual([0, 1])
    expect(rows[0]).toEqual({ port: 0, opackets: 120712230, ipackets: 7, obytes: 7725582720, ibytes: 518, tx_bps: 448.4, rx_bps: 5.405e-07,
      tx_pps: 0.8759, rx_pps: 9.131e-10, ierrors: 0, oerrors: 0 })
    expect(rows[1].ierrors).toBe(202208)
    expect(portRows((stateful as unknown as MonitorMsg).global).map((r) => r.port)).toEqual([0, 1])
  })

  it('orders ports numerically past nine', () => {
    const many = Object.fromEntries([2, 10, 0].map((n) => [`opackets-${n}`, n]))
    expect(portRows(many).map((r) => r.port)).toEqual([0, 2, 10])
  })
})

describe('monitorLayouts stateful panels', () => {
  const st = stateful as unknown as MonitorMsg
  it('templateRows takes tx from trex-global and the rest from rx-check', () => {
    const rows = templateRows(st.templates!, st.global, st.rx_check)
    const tx = (st.global as { template: number[] }).template
    const rc = (st.rx_check as { template: { val: number; rx_pkts: number; jitter: number }[] }).template
    expect(rows).toHaveLength(3)
    expect(rows.map((r) => r.tx)).toEqual([tx[0], tx[1], tx[2]])
    expect(rows[2]).toMatchObject({ name: 'cap2/dns.pcap', tx: 2, rx: 2, errors: 0, jitter: 212, flagged: false })
    expect(rows.map((r) => [r.errors, r.rx, r.jitter])).toEqual(rc.slice(0, 3).map((e) => [e.val, e.rx_pkts, e.jitter]))
    // without rx-check, tx stays and the rest is null
    expect(templateRows(st.templates!, st.global, null)[2]).toEqual({ name: 'cap2/dns.pcap', tx: 2, rx: null, errors: null, jitter: null, flagged: false })
  })

  it('templateRows flags by the legacy rule', () => {
    const run = (tx: number, rx: number, val: number) =>
      templateRows(['t'], { template: [tx] }, { template: [{ id: 0, val, rx_pkts: rx, jitter: 0 }] })[0].flagged
    expect(run(400, 200, 0)).toBe(true)     // rx/tx 0.5 with tx > 300
    expect(run(200, 100, 0)).toBe(false)    // tx not above 300
    expect(run(400, 300, 0)).toBe(false)    // rx/tx 0.75
    expect(run(10, 10, 101)).toBe(true)     // errors above 100
    expect(run(10, 10, 100)).toBe(false)
    expect(run(0, 0, 500)).toBe(false)      // nothing sent
    expect(templateRows(['t'], { template: [400] }, null)[0].flagged).toBe(false)   // no rx-check: never flagged
  })

  it('stfLatencyRows splits client and server', () => {
    const rows = stfLatencyRows(st.latency)
    expect(rows.map((r) => [r.port, r.side])).toEqual([[0, 'client'], [1, 'server']])
    expect(rows[0]).toEqual({ port: 0, side: 'client', avg: 552, max: 1049, cmax: 1047.6, error: 0, jitter: 250 })
    expect(stfLatencyRows(null)).toEqual([])
  })

  it('clientServerMax uses c-max, the period maximum', () => {
    expect(clientServerMax({ 'c-max-0': 5, 'c-max-2': 9, 'c-max-1': 3, 'max-0': 1000, 'max-1': 2000 })).toEqual({ client: 9, server: 3 })
    expect(clientServerMax({ 'max-0': 1000 })).toEqual({ client: null, server: null })
  })
})
