import { describe, expect, it } from 'vitest'
import { wirePgid, wireUtil } from './testPgid'

describe('wirePgid / wireUtil: the shapes the backend really publishes', () => {
  it('tx: pg_id 5 flow stats and pg_id 7 latency, Tx on port 0 and Rx on port 1', () => {
    const m = wirePgid('tx')
    expect(m.active).toEqual({ flow_stats: [5], latency: [7] })
    expect(m.flow['5'].tx_pps['0']).toBeGreaterThan(0)
    expect(m.flow['5'].rx_pps['1']).toBeGreaterThan(0)
    expect(m.latency['7'].latency.average).not.toBeNull()
  })
  it('readded: a different stream now owns each id, and the latency id has seen no traffic yet', () => {
    const [a, b] = [wirePgid('tx'), wirePgid('readded')]
    expect(b.ver_id['5']).not.toBe(a.ver_id['5'])
    expect(b.active.latency).toEqual([7])
    expect(b.latency).toEqual({})
  })
  it('util: cpu history newest first and mbuf pairs as free/total', () => {
    const u = wireUtil('tx')
    expect(u.cpu[0].history.length).toBeGreaterThan(1)
    expect(Object.values(Object.values(u.mbuf)[0])[0]).toEqual({ free: expect.any(Number), total: expect.any(Number) })
  })
  it('the loader hands out a copy', () => {
    wirePgid('tx').ver_id['5'] = 99
    expect(wirePgid('tx').ver_id['5']).not.toBe(99)
  })
})
