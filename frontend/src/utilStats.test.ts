import { beforeEach, describe, expect, it } from 'vitest'
import type { UtilMsg } from './api'
import { newSamples, utilStore } from './utilStats'
import { wireUtil } from './testPgid'

const at = (ts: number, history?: (number | null)[]): UtilMsg => {
  const m = wireUtil('tx') as UtilMsg
  m.ts = ts
  if (history) m.cpu[0].history = history
  return m
}

beforeEach(() => { utilStore.reset() })

describe('utilStore', () => {
  it('cpuSeries seeds from history, then appends history[0], and skips a repeated sample', () => {
    const h = wireUtil('tx').cpu[0].history
    utilStore.push(at(100))
    const seeded = utilStore.cpuSeries(0, 1000)
    expect(seeded).toHaveLength(20)
    expect(seeded[19]).toEqual({ ts: 100, v: h[0] })           // history[0] is the newest
    expect(seeded[0]).toEqual({ ts: 81, v: h[19] })            // one point per second back from ts
    expect(utilStore.cpuSeries(0, 3).map((p) => p.ts)).toEqual([98, 99, 100])

    utilStore.push(at(101, [9, ...h.slice(0, 19)]))            // the server took a new sample
    expect(utilStore.cpuSeries(0, 2)).toEqual([{ ts: 100, v: h[0] }, { ts: 101, v: 9 }])

    utilStore.push(at(102, [9, ...h.slice(0, 19)]))            // same history: nothing new to plot
    expect(utilStore.cpuSeries(0, 1000)).toHaveLength(21)
    utilStore.push(at(103, [7, 9, ...h.slice(0, 18)]))
    expect(utilStore.cpuSeries(0, 1)).toEqual([{ ts: 103, v: 7 }])
  })

  it('an idle (constant) history polled at 1 s steps keeps advancing', () => {
    const zeros = (m: UtilMsg) => { m.cpu[0].history = Array<number | null>(20).fill(0) }
    utilStore.push(at(0))
    const first = at(1); zeros(first); utilStore.push(first)
    for (let t = 2; t <= 30; t++) { const m = at(t); zeros(m); utilStore.push(m) }
    expect(utilStore.cpuSeries(0, 10).map((p) => p.ts)).toEqual([21, 22, 23, 24, 25, 26, 27, 28, 29, 30])
    // a poll 3 s later adds 3 points; one at the same ts adds none
    const late = at(33); zeros(late); utilStore.push(late)
    expect(utilStore.cpuSeries(0, 4).map((p) => p.ts)).toEqual([30, 31, 32, 33])
    const dup = at(33); zeros(dup); utilStore.push(dup)
    expect(utilStore.cpuSeries(0, 4)).toHaveLength(4)
    const all = utilStore.cpuSeries(0, 100000).map((p) => p.ts)
    expect(all.every((t, i) => i === 0 || t > all[i - 1])).toBe(true)
  })

  it('ts strictly increases even when a non-matching history overlaps the seed', () => {
    utilStore.push(at(0))
    utilStore.push(at(1, Array<number | null>(20).fill(0)))
    const ts = utilStore.cpuSeries(0, 100000).map((p) => p.ts)
    expect(ts.length).toBeGreaterThan(20)
    expect(ts.every((t, i) => i === 0 || t > ts[i - 1])).toBe(true)
    expect(ts[ts.length - 1]).toBe(1)
  })

  it('newSamples: a different length is all new, an empty history is none', () => {
    const h = wireUtil('tx').cpu[0].history
    expect(newSamples(h, h.slice(0, 19), 5, 4)).toBe(20)
    expect(newSamples(h.slice(0, 19), h, 5, 4)).toBe(19)
    expect(newSamples([], h, 5, 4)).toBe(0)
    expect(newSamples([], [], 5, 4)).toBe(0)
  })

  it('a 2-step shift appends 2 points with the right values and ts; an unchanged non-constant history appends none', () => {
    const h = wireUtil('tx').cpu[0].history
    utilStore.push(at(100))
    utilStore.push(at(102, [8, 9, ...h.slice(0, 18)]))
    expect(utilStore.cpuSeries(0, 3)).toEqual([{ ts: 100, v: h[0] }, { ts: 101, v: 9 }, { ts: 102, v: 8 }])
    utilStore.push(at(103, [8, 9, ...h.slice(0, 18)]))
    expect(utilStore.cpuSeries(0, 1000)).toHaveLength(22)
  })

  it('a history that fits no shift is appended whole', () => {
    utilStore.push(at(100))
    utilStore.push(at(200, Array.from({ length: 20 }, (_, i) => 50 + i)))
    const s = utilStore.cpuSeries(0, 20)
    expect(s).toHaveLength(20)
    expect(s[19]).toEqual({ ts: 200, v: 50 })
  })

  it('onSession resets on a transition into connecting or disconnected only', () => {
    utilStore.onSession('connected')
    utilStore.push(at(1))
    utilStore.onSession('connected')
    expect(utilStore.latest()).not.toBeNull()
    utilStore.onSession('lost')
    expect(utilStore.latest()).not.toBeNull()
    utilStore.onSession('connecting')
    expect(utilStore.latest()).toBeNull()
  })

  it('keeps 300 points, tolerates a null sample, and reports an unknown core as empty', () => {
    utilStore.push(at(0))
    for (let i = 1; i <= 310; i++) utilStore.push(at(i, [i, ...Array<number | null>(18).fill(0), null]))
    const s = utilStore.cpuSeries(0, 100000)
    expect(s).toHaveLength(300)
    expect(s[299].ts).toBe(310)
    expect(utilStore.cpuSeries(5, 100)).toEqual([])
  })

  it('latest, receivedAt and reset', () => {
    expect(utilStore.latest()).toBeNull()
    expect(utilStore.receivedAt()).toBeNull()
    utilStore.push(at(1))
    expect(utilStore.latest()?.ts).toBe(1)
    expect(utilStore.receivedAt()).not.toBeNull()
    utilStore.reset()
    expect(utilStore.latest()).toBeNull()
    expect(utilStore.cpuSeries(0, 100)).toEqual([])
  })
})

describe('utilStore subscribe', () => {
  it('notifies on push and reset, bumps version, and stops after unsubscribe', () => {
    let n = 0
    const off = utilStore.subscribe(() => { n++ })
    const v0 = utilStore.version()
    utilStore.push(at(100))
    expect(n).toBe(1)
    expect(utilStore.version()).toBe(v0 + 1)
    utilStore.reset()
    expect(n).toBe(2)
    off()
    utilStore.push(at(101))
    expect(n).toBe(2)
  })
})
