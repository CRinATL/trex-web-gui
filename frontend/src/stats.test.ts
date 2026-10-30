import { beforeEach, describe, expect, it } from 'vitest'
import type { StatsMsg } from './api'
import { statsStore } from './stats'

const msg = (ts: number, extra: Partial<StatsMsg> = {}): StatsMsg => ({
  type: 'stats', ts, interval_s: 1, global: { cpu_util: ts }, ports: { '0': { opackets: ts } }, total: { opackets: ts * 2 }, ...extra,
})

beforeEach(() => { statsStore.onSession('connected'); statsStore.onSession('connecting') })

describe('statsStore', () => {
  it('receivedAt is null when empty, set on push, and cleared by a reset', () => {
    expect(statsStore.receivedAt()).toBeNull()
    statsStore.push(msg(1))
    expect(statsStore.receivedAt()).toBeGreaterThan(0)
    statsStore.onSession('disconnected')
    expect(statsStore.receivedAt()).toBeNull()
  })
  it('caps at 300 points and drops the oldest', () => {
    for (let i = 0; i < 305; i++) statsStore.push(msg(i))
    const s = statsStore.series('global', 10000)
    expect(s).toHaveLength(300)
    expect(s[0].ts).toBe(5)
    expect(s[299].ts).toBe(304)
  })

  it('series returns the last N seconds by ts, oldest first, per scope', () => {
    for (let i = 1; i <= 20; i++) statsStore.push(msg(i))
    expect(statsStore.series('global', 5).map((p) => p.ts)).toEqual([16, 17, 18, 19, 20])
    expect(statsStore.series('0', 3)).toEqual([{ ts: 18, opackets: 18 }, { ts: 19, opackets: 19 }, { ts: 20, opackets: 20 }])
    expect(statsStore.series('total', 1)).toEqual([{ ts: 20, opackets: 40 }])
  })

  it('series is [] when empty or when the scope is missing', () => {
    expect(statsStore.series('global', 10)).toEqual([])
    statsStore.push(msg(1))
    expect(statsStore.series('7', 10)).toEqual([])
  })

  it('onSession(connecting) after connected empties the store; disconnected does too', () => {
    statsStore.onSession('connected')
    statsStore.push(msg(1))
    statsStore.onSession('connecting')
    expect(statsStore.latest()).toBeNull()
    expect(statsStore.series('global', 10)).toEqual([])
    statsStore.onSession('connected')
    statsStore.push(msg(2))
    statsStore.onSession('disconnected')
    expect(statsStore.latest()).toBeNull()
  })

  it('does not reset on connected or lost, and version rises on every push and reset', () => {
    const v0 = statsStore.version()
    statsStore.push(msg(1))
    expect(statsStore.version()).toBe(v0 + 1)
    statsStore.onSession('lost')
    expect(statsStore.latest()?.ts).toBe(1)
    expect(statsStore.version()).toBe(v0 + 1)
    statsStore.onSession('disconnected')
    expect(statsStore.version()).toBe(v0 + 2)
  })

  it('hands out the same snapshot object until the next change', () => {
    statsStore.push(msg(1))
    expect(statsStore.snapshot()).toBe(statsStore.snapshot())
    const a = statsStore.snapshot()
    statsStore.push(msg(2))
    expect(statsStore.snapshot()).not.toBe(a)
  })

  describe('clear and relative', () => {
    const full = (n: number, bps = 5): StatsMsg => msg(n, {
      global: { cpu_util: 1, queue_full: n }, total: { opackets: n * 2, tx_bps_L1: bps, rx_bps_L1: bps },
      ports: { '0': { opackets: n, ibytes: n * 10, tx_bps_L1: bps, rx_bps_L1: bps } },
    })
    it('counters read 0 after clear, then grow by the next delta', () => {
      statsStore.push(full(100))
      expect(statsStore.relative('0', 'opackets')).toBe(100)
      statsStore.clear()
      expect(statsStore.relative('0', 'opackets')).toBe(0)
      expect(statsStore.relative('total', 'opackets')).toBe(0)   // sum of the ports (only port 0 here)
      statsStore.push(full(130))
      expect(statsStore.relative('0', 'opackets')).toBe(30)
      expect(statsStore.relative('0', 'ibytes')).toBe(300)
      expect(statsStore.relative('total', 'opackets')).toBe(30)
    })
    it('rates and gauges (and queue_full) are never baselined', () => {
      statsStore.push(full(100, 7))
      statsStore.clear()
      expect(statsStore.relative('0', 'tx_bps_L1')).toBe(7)
      expect(statsStore.relative('0', 'rx_bps_L1')).toBe(7)
      expect(statsStore.relative('total', 'tx_bps_L1')).toBe(7)
      expect(statsStore.relative('global', 'queue_full')).toBe(100)
      expect(statsStore.relative('global', 'cpu_util')).toBe(1)
    })
    it('raw below the baseline returns raw and resets that baseline (no negative)', () => {
      statsStore.push(full(100))
      statsStore.clear()
      statsStore.push(full(5))    // the client's counters were zeroed by a Reset, or a missed reconnect
      expect(statsStore.relative('0', 'opackets')).toBe(5)
      statsStore.push(full(150))
      expect(statsStore.relative('0', 'opackets')).toBe(150)   // baseline is now 0, not 100 (which would give 50)
    })
    it('null raw, an unknown scope and an empty store give null; clear on an empty store is a no-op', () => {
      const v0 = statsStore.version()
      statsStore.clear()
      expect(statsStore.version()).toBe(v0)
      expect(statsStore.relative('0', 'opackets')).toBeNull()
      statsStore.push(msg(1, { ports: { '0': { opackets: null } } }))
      statsStore.clear()
      expect(statsStore.relative('0', 'opackets')).toBeNull()
      expect(statsStore.relative('9', 'opackets')).toBeNull()
    })
    it('a drop is noticed with no cell mounted: later growth past the old baseline is not lost', () => {
      statsStore.push(full(100))
      statsStore.clear()
      statsStore.push(full(5))     // Reset while the Dashboard is hidden: nobody calls relative()
      statsStore.push(full(150))
      expect(statsStore.relative('0', 'opackets')).toBe(150)   // 50 if the baseline were still 100
    })
    it('after a Reset of one port, Total is the sum of its ports', () => {
      const two = (a: number, b: number): StatsMsg => msg(a, { global: {}, total: { opackets: a + b }, ports: { '0': { opackets: a }, '1': { opackets: b } } })
      statsStore.push(two(100, 200))
      statsStore.clear()
      statsStore.push(two(10, 250))     // port 0 was Reset (100 -> 10); port 1 grew by 50
      statsStore.push(two(30, 270))
      expect(statsStore.relative('0', 'opackets')).toBe(30)
      expect(statsStore.relative('1', 'opackets')).toBe(70)
      expect(statsStore.relative('total', 'opackets')).toBe(100)
    })
    it('Total skips a null port and is null only when every port is null', () => {
      statsStore.push(msg(1, { ports: { '0': { opackets: 5 }, '1': { opackets: null } } }))
      statsStore.clear()
      statsStore.push(msg(2, { ports: { '0': { opackets: 8 }, '1': { opackets: null } } }))
      expect(statsStore.relative('total', 'opackets')).toBe(3)
      statsStore.push(msg(3, { ports: { '0': { opackets: null }, '1': { opackets: null } } }))
      expect(statsStore.relative('total', 'opackets')).toBeNull()
    })
    it('clear bumps the version so selectors re-render; a session reset drops the baselines', () => {
      statsStore.push(full(100))
      const v = statsStore.version()
      statsStore.clear()
      expect(statsStore.version()).toBe(v + 1)
      statsStore.onSession('connected'); statsStore.onSession('connecting')
      statsStore.push(full(100))
      expect(statsStore.relative('0', 'opackets')).toBe(100)
    })
  })
})

describe('statsStore stats_epoch', () => {
  it('a stats_epoch change drops every baseline, including those a drop to 0 would miss', () => {
    statsStore.push(msg(1, { stats_epoch: 3 }))
    statsStore.push(msg(2, { stats_epoch: 3, ports: { '0': { opackets: 1000 } } }))
    statsStore.clear()
    statsStore.push(msg(3, { stats_epoch: 3, ports: { '0': { opackets: 1010 } } }))
    expect(statsStore.relative('0', 'opackets')).toBe(10)
    // A Reset (from any browser) zeroed the server's counters, and they regrew past the baseline within one interval.
    statsStore.push(msg(4, { stats_epoch: 4, ports: { '0': { opackets: 1500 } } }))
    expect(statsStore.relative('0', 'opackets')).toBe(1500)
    statsStore.push(msg(5, { stats_epoch: 4, ports: { '0': { opackets: 1600 } } }))
    expect(statsStore.relative('0', 'opackets')).toBe(1600)
  })
  it('the first epoch seen is not a change, and a message without one never is', () => {
    statsStore.push(msg(1))
    statsStore.clear()
    statsStore.push(msg(2, { stats_epoch: 7, ports: { '0': { opackets: 5 } } }))   // epoch first seen
    expect(statsStore.relative('0', 'opackets')).toBe(4)
    statsStore.clear()
    statsStore.push(msg(3, { ports: { '0': { opackets: 9 } } }))
    expect(statsStore.relative('0', 'opackets')).toBe(4)
  })
  it('reset empties the buffer, the baselines and the epoch', () => {
    statsStore.push(msg(1, { stats_epoch: 7 }))
    statsStore.clear()
    statsStore.reset()
    expect(statsStore.latest()).toBeNull()
    statsStore.push(msg(2, { stats_epoch: 8 }))
    expect(statsStore.relative('0', 'opackets')).toBe(2)
  })
})
