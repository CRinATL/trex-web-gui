import { beforeEach, describe, expect, it } from 'vitest'
import type { PgidMsg } from './api'
import { pgidStore } from './pgidStats'
import { wirePgid, type PgidScenario } from './testPgid'

// A recorded scenario with its own ts; `edit` derives variants from the copy (the fixtures are never hand-written).
const at = (name: PgidScenario, ts: number, edit?: (m: PgidMsg) => void): PgidMsg => {
  const m = wirePgid(name) as PgidMsg
  m.ts = ts
  edit?.(m)
  return m
}
const lastMax = (v: number) => (m: PgidMsg) => { m.latency['7'].latency.last_max = v }

beforeEach(() => { pgidStore.reset(); pgidStore.track('streams', []); pgidStore.track('latency', []) })

describe('pgidStore', () => {
  it('a ver_id change drops history, baseline, window and running max', () => {
    pgidStore.track('streams', [5])
    pgidStore.push(at('tx', 1, lastMax(900)))
    pgidStore.push(at('tx', 2, lastMax(1135)))
    pgidStore.clear()
    pgidStore.push(at('tx', 3, (m) => { lastMax(400)(m); m.flow['5'].tx_pkts.total = 90919 }))
    expect(pgidStore.relative(5, 'tx_pkts', 'total')).toBe(10)
    expect(pgidStore.series(5, 'tx_pps', 100)).toHaveLength(3)
    expect(pgidStore.window(7)).toEqual([400])

    // pg_id 5 and 7 are now owned by other streams
    pgidStore.push(at('tx', 4, (m) => { m.ver_id = { '5': 9, '7': 8 }; lastMax(300)(m) }))
    expect(pgidStore.relative(5, 'tx_pkts', 'total')).toBe(90909)   // the baseline is gone
    expect(pgidStore.series(5, 'tx_pps', 100).map((p) => p.ts)).toEqual([4])
    expect(pgidStore.window(7)).toEqual([300])
    expect(pgidStore.runningMax(7)).toBe(300)
  })

  it('untracked pg_ids keep only the latest sample, but every latency pg_id keeps its window and running max', () => {
    pgidStore.track('streams', [5])
    for (const [ts, v] of [[1, 700], [2, 1135], [3, 800]]) pgidStore.push(at('tx', ts, lastMax(v)))
    expect(pgidStore.series(5, 'tx_pps', 100)).toHaveLength(3)
    expect(pgidStore.series(7, 'tx_pps', 100)).toEqual([])
    expect(pgidStore.latest()?.flow['7'].tx_pps.total).toBe(909.09)
    expect(pgidStore.window(7)).toEqual([800, 1135, 700])
    expect(pgidStore.runningMax(7)).toBe(1135)
    expect(pgidStore.ids()).toEqual([5, 7])
    expect(pgidStore.latencyIds()).toEqual([7])
  })

  it('the two tabs track separately, up to 16 in all', () => {
    const many = (m: PgidMsg) => {
      for (let id = 1; id <= 20; id++) { m.flow[String(id)] = m.flow['5']; m.ver_id[String(id)] = 0 }
    }
    pgidStore.track('streams', [1, 2, 3, 4, 5, 6, 7, 8, 9, 10])   // only the first 8 count
    pgidStore.track('latency', [11, 12, 13, 14, 15, 16, 17, 18, 19, 20])
    pgidStore.push(at('tx', 1, many))
    const has = (id: number) => pgidStore.series(id, 'tx_pps', 100).length === 1
    expect([1, 8, 11, 18].every(has)).toBe(true)
    expect([9, 10, 19, 20].some(has)).toBe(false)
    pgidStore.track('latency', [])   // dropping one tab's selection drops only its history
    expect([1, 8].every(has)).toBe(true)
    expect([11, 18].some(has)).toBe(false)
  })

  it('loss is floored at zero', () => {
    pgidStore.track('streams', [5])
    pgidStore.push(at('tx', 1, (m) => { m.flow['5'].rx_pps.total = 9100; m.flow['5'].rx_pkts.total = 90915 }))
    expect(pgidStore.series(5, 'loss_pps', 100)[0].v).toBe(0)
    expect(pgidStore.lossPkts(5)).toBe(0)
    pgidStore.push(at('tx', 2, (m) => { m.flow['5'].rx_pps.total = 9000; m.flow['5'].rx_pkts.total = 90000 }))
    expect(pgidStore.series(5, 'loss_pps', 100)[1].v).toBeCloseTo(90.91, 2)
    expect(pgidStore.lossPkts(5)).toBe(909)
  })

  it('the latency window holds 10, newest first', () => {
    for (let i = 1; i <= 12; i++) pgidStore.push(at('tx', i, lastMax(i)))
    expect(pgidStore.window(7)).toEqual([12, 11, 10, 9, 8, 7, 6, 5, 4, 3])
    expect(pgidStore.runningMax(7)).toBe(12)
  })

  it('Clear zeroes flow, error and histogram counters but not rates', () => {
    pgidStore.push(at('tx', 1, (m) => { m.latency['7'].err_cntrs.dup = 4 }))
    pgidStore.clear()
    for (const f of ['tx_pkts', 'rx_pkts', 'tx_bytes', 'rx_bytes'] as const) expect(pgidStore.relative(5, f, 'total')).toBe(0)
    expect(pgidStore.relative(5, 'rx_pkts', '1')).toBe(0)
    expect(pgidStore.relativeErr(7, 'dup')).toBe(0)
    expect(Object.values(pgidStore.relativeHist(7)).every((n) => n === 0)).toBe(true)
    expect(Object.keys(pgidStore.relativeHist(7)).length).toBeGreaterThan(0)
    expect(pgidStore.latest()?.flow['5'].tx_pps.total).toBe(9090.91)
    expect(pgidStore.window(7)).toEqual([])
    expect(pgidStore.runningMax(7)).toBeNull()
    pgidStore.push(at('tx', 2, (m) => { m.flow['5'].tx_pkts.total = 90909 + 100; m.latency['7'].err_cntrs.dup = 6; m.latency['7'].latency.histogram['100'] += 3 }))
    expect(pgidStore.relative(5, 'tx_pkts', 'total')).toBe(100)
    expect(pgidStore.relativeErr(7, 'dup')).toBe(2)
    expect(pgidStore.relativeHist(7)['100']).toBe(3)
  })

  it('dropped decreasing by 1 after Clear shows 0, not a jump', () => {
    pgidStore.push(at('tx', 1, (m) => { m.latency['7'].err_cntrs.dropped = 3 }))
    pgidStore.clear()
    pgidStore.push(at('tx', 2, (m) => { m.latency['7'].err_cntrs.dropped = 2 }))   // a late packet arrived
    expect(pgidStore.relativeErr(7, 'dropped')).toBe(0)
    pgidStore.push(at('tx', 3, (m) => { m.latency['7'].err_cntrs.dropped = 5 }))
    expect(pgidStore.relativeErr(7, 'dropped')).toBe(2)   // the baseline stayed at 3
  })

  it('a flow counter or histogram count below its baseline drops that baseline to 0', () => {
    pgidStore.push(at('tx', 1))
    pgidStore.clear()
    // same ver_id, counters zeroed under it (as readded has them)
    pgidStore.push(at('readded', 2, (m) => { m.ver_id = { '5': 0, '7': 1 } }))
    expect(pgidStore.relative(5, 'tx_pkts', 'total')).toBe(0)
    pgidStore.push(at('tx', 3, (m) => { m.flow['5'].tx_pkts.total = 5; m.latency['7'].latency.histogram['100'] = 2 }))
    expect(pgidStore.relative(5, 'tx_pkts', 'total')).toBe(5)
    expect(pgidStore.relativeHist(7)['100']).toBe(2)
  })

  it('an id with no ver_id entry keeps its last known ver_id, and state for ids in neither flow nor ver_id is pruned', () => {
    pgidStore.push(at('tx', 1, lastMax(900)))
    pgidStore.clear()
    pgidStore.push(at('tx', 2, (m) => { m.ver_id = {}; m.flow['5'].tx_pkts.total = 90919 }))   // no ver_id entries at all
    expect(pgidStore.relative(5, 'tx_pkts', 'total')).toBe(10)   // the baseline survived
    pgidStore.push(at('tx', 3, (m) => { m.ver_id = { '5': 9 } }))   // 5 now has a new ver_id: dropped, so the old one was kept
    expect(pgidStore.relative(5, 'tx_pkts', 'total')).toBe(90909)
    // 7 left both maps in this message: its window is gone; 5 stays
    pgidStore.push(at('tx', 4, (m) => { delete m.flow['7']; delete m.ver_id['7']; delete m.latency['7'] }))
    expect(pgidStore.window(7)).toEqual([])
    pgidStore.push(at('tx', 5, lastMax(77)))
    expect(pgidStore.window(7)).toEqual([77])
  })

  it('a latency pg_id with no samples yet reads null, not NaN', () => {
    pgidStore.push(at('readded', 1))
    expect(pgidStore.relativeErr(7, 'dropped')).toBeNull()
    expect(pgidStore.relativeHist(7)).toEqual({})
    expect(pgidStore.window(7)).toEqual([])
    expect(pgidStore.runningMax(7)).toBeNull()
  })

  it('reset empties the store; onSession resets only on a transition into connecting or disconnected', () => {
    pgidStore.onSession('connected')
    pgidStore.push(at('tx', 1))
    pgidStore.onSession('connected')
    expect(pgidStore.latest()).not.toBeNull()
    expect(pgidStore.receivedAt()).not.toBeNull()
    pgidStore.onSession('connecting')
    expect(pgidStore.latest()).toBeNull()
    pgidStore.push(at('tx', 2))
    pgidStore.reset()
    expect(pgidStore.latest()).toBeNull()
    expect(pgidStore.receivedAt()).toBeNull()
    expect(pgidStore.ids()).toEqual([])
  })

  it('reset also forgets the tracked pg_ids, so a later push keeps no chart history for them', () => {
    pgidStore.track('streams', [5])
    pgidStore.reset()
    pgidStore.push(at('tx', 1))
    expect(pgidStore.series(5, 'tx_pps', 100)).toEqual([])
  })

  it('notifies subscribers and bumps version on push and clear', () => {
    let n = 0
    const off = pgidStore.subscribe(() => { n++ })
    const v = pgidStore.version()
    pgidStore.push(at('tx', 1))
    pgidStore.clear()
    off()
    pgidStore.push(at('tx', 2))
    expect(n).toBe(2)
    expect(pgidStore.version()).toBe(v + 3)
  })
})

describe('flow_global baselines and lossPps', () => {
  it('Clear baselines rx_err/tx_err, a drop below the baseline rebases to 0, reset forgets them', () => {
    pgidStore.reset()
    const m = wirePgid('tx'); m.flow_global.tx_err['0'] = 4
    pgidStore.push(m)
    expect(pgidStore.relativeFlowGlobal('tx_err', '0')).toBe(4)
    pgidStore.clear()
    expect(pgidStore.relativeFlowGlobal('tx_err', '0')).toBe(0)
    const m2 = wirePgid('tx'); m2.flow_global.tx_err['0'] = 1
    pgidStore.push(m2)
    expect(pgidStore.relativeFlowGlobal('tx_err', '0')).toBe(1)
    expect(pgidStore.relativeFlowGlobal('tx_err', '9')).toBeNull()
    pgidStore.reset()
  })
  it('lossPps is Tx pps minus Rx pps floored at 0, null when either is missing', () => {
    pgidStore.reset()
    const m = wirePgid('tx'); m.flow['5'].rx_pps.total = 9000
    pgidStore.push(m)
    expect(pgidStore.lossPps(5)).toBeCloseTo(90.91)
    expect(pgidStore.lossPps(99)).toBeNull()
    m.flow['5'].rx_pps.total = 99999
    pgidStore.push(m)
    expect(pgidStore.lossPps(5)).toBe(0)
    pgidStore.reset()
  })
})
