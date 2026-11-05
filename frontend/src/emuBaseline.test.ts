import { beforeEach, describe, expect, it } from 'vitest'
import type { EmuCounterRow } from './api'
import { baselineKey, deltaOf, EMU_BASELINE_KEY, readBaselines, storeBaseline, takeBaseline } from './emuBaseline'

const ns = { vport: 1, tci: [0, 0], tpid: [0, 0] }
const row = (name: string, value: number | null): EmuCounterRow => ({ name, value, unit: 'pkts', help: '', info: 0x12, zero: value === 0 })

beforeEach(() => localStorage.clear())

describe('emuBaseline', () => {
  it('keys a baseline by scope, namespace and client', () => {
    expect(baselineKey('ctx', null, null)).toBe('ctx')
    expect(baselineKey('ns', ns, null)).toBe('ns:1/0,0/0,0')
    expect(baselineKey('client', ns, [0, 0, 0, 1, 0, 2])).toBe('client:1/0,0/0,0:00:00:00:01:00:02')
    expect(baselineKey('ns', { vport: 2 }, null)).not.toBe(baselineKey('ns', ns, null))
  })
  it('stores and reads back, dropping malformed entries', () => {
    storeBaseline('ctx', { at: 5, values: { 'a/b': 1 } })
    expect(readBaselines()).toEqual({ ctx: { at: 5, values: { 'a/b': 1 } } })
    localStorage.setItem(EMU_BASELINE_KEY, JSON.stringify({ ok: { at: 1, values: {} }, bad: 3, worse: { at: 'x' } }))
    expect(Object.keys(readBaselines())).toEqual(['ok'])
    localStorage.setItem(EMU_BASELINE_KEY, '[1')
    expect(readBaselines()).toEqual({})
  })
  it('keeps the newest 50', () => {
    for (let i = 0; i < 55; i++) storeBaseline(`k${i}`, { at: i, values: {} })
    const keys = Object.keys(readBaselines())
    expect(keys).toHaveLength(50)
    expect(keys).not.toContain('k0')
    expect(keys).toContain('k54')
  })
  it('takes values by table/name and leaves null values out; a delta of an absent counter is its total', () => {
    expect(takeBaseline({ t: [row('a', 3), row('b', null)] }, 9)).toEqual({ at: 9, values: { 't/a': 3 } })
    expect(deltaOf(10, 4)).toBe(6)
    expect(deltaOf(10, undefined)).toBe(10)
    expect(deltaOf(null, 4)).toBeNull()
  })
})
