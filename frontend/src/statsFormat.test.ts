import { describe, expect, it } from 'vitest'
import { COUNTER_KEYS, cell, humanBps, humanPps, l1Bps } from './statsFormat'

describe('statsFormat', () => {
  it('l1Bps adds 160 bits per packet and passes null through', () => {
    expect(l1Bps(1e9, 1e6)).toBe(1e9 + 1e6 * 160)
    expect(l1Bps(null, 5)).toBeNull()
    expect(l1Bps(5, null)).toBeNull()
  })
  it('humanBps scales and never prints NaN', () => {
    expect(humanBps(0)).toBe('0 bps')
    expect(humanBps(999)).toBe('999 bps')
    expect(humanBps(1e3)).toBe('1.00 kbps')
    expect(humanBps(1.5e9)).toBe('1.50 Gbps')
    for (const v of [null, NaN, 'N/A', undefined, -1, Infinity]) expect(humanBps(v)).toBe('–')
  })
  it('humanBps picks the unit from the rounded value', () => {
    expect(humanBps(999.995)).toBe('1.00 kbps')
    expect(humanBps(999995)).toBe('1.00 Mbps')
    expect(humanBps(999)).toBe('999 bps')
  })
  it('humanPps uses its own unit', () => {
    expect(humanPps(2e6)).toBe('2.00 Mpps')
    expect(humanPps(null)).toBe('–')
  })
  it('cell shows a dash for null, N/A, NaN and negatives', () => {
    for (const v of [null, 'N/A', NaN, -3]) expect(cell(v)).toBe('–')
    expect(cell(1234)).toBe('1,234')
    expect(cell(0)).toBe('0')
  })
  it('COUNTER_KEYS is the six port counters', () => {
    expect(COUNTER_KEYS).toEqual(['opackets', 'ipackets', 'obytes', 'ibytes', 'oerrors', 'ierrors'])
  })
})
