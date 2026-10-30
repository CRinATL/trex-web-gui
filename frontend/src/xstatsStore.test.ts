import { beforeEach, describe, expect, it } from 'vitest'
import { xstatsStore } from './xstatsStore'

beforeEach(() => xstatsStore.clear())

describe('xstatsStore', () => {
  it('relative is value minus baseline; reset makes the baseline the current value', () => {
    xstatsStore.push({ port: 0, values: { a: 10, b: 5 } })
    expect(xstatsStore.relative(0, 'a')).toBe(10)
    xstatsStore.reset(0)
    expect(xstatsStore.relative(0, 'a')).toBe(0)
    xstatsStore.push({ port: 0, values: { a: 17, b: 5 } })
    expect(xstatsStore.relative(0, 'a')).toBe(7)
    expect(xstatsStore.relative(0, 'b')).toBe(0)
  })

  it('keeps ports apart and pins per port', () => {
    xstatsStore.push({ port: 0, values: { a: 1 } })
    xstatsStore.push({ port: 1, values: { a: 9 } })
    xstatsStore.reset(1)
    xstatsStore.pin(0, 'a'); xstatsStore.pin(0, 'a')
    expect(xstatsStore.relative(0, 'a')).toBe(1)
    expect(xstatsStore.relative(1, 'a')).toBe(0)
    expect(xstatsStore.pins(0)).toEqual(['a'])
    expect(xstatsStore.pins(1)).toEqual([])
    xstatsStore.unpin(0, 'a')
    expect(xstatsStore.pins(0)).toEqual([])
  })

  it('clear drops values, baselines and pins; subscribers are told', () => {
    let n = 0
    const off = xstatsStore.subscribe(() => { n++ })
    xstatsStore.push({ port: 0, values: { a: 4 } })
    xstatsStore.reset(0); xstatsStore.pin(0, 'a')
    const before = n
    xstatsStore.clear()
    expect(n).toBe(before + 1)
    expect(xstatsStore.relative(0, 'a')).toBe(0)
    expect(xstatsStore.pins(0)).toEqual([])
    expect(xstatsStore.names(0)).toEqual([])
    off()
  })
})
