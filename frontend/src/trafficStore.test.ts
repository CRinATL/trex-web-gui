import { afterEach, describe, expect, it, vi } from 'vitest'
import { DEFAULT_FORM, trafficStore } from './trafficStore'

afterEach(() => trafficStore.reset())

describe('trafficStore', () => {
  it('get returns the default for an unknown key', () => {
    expect(trafficStore.get(3)).toEqual(DEFAULT_FORM)
    expect(trafficStore.get(3)).toBe(trafficStore.get(3))   // a stable snapshot for useSyncExternalStore
    expect(trafficStore.all()).toEqual({})
  })
  it('set patches one port only', () => {
    trafficStore.set(0, { path: '/a.py', multValue: '5' })
    trafficStore.set(0, { force: true })
    expect(trafficStore.get(0)).toEqual({ ...DEFAULT_FORM, path: '/a.py', multValue: '5', force: true })
    expect(trafficStore.get(1)).toEqual(DEFAULT_FORM)
    expect(Object.keys(trafficStore.all())).toEqual(['0'])
  })
  it('set makes a new object for that port and leaves the others identical', () => {
    trafficStore.set(0, { path: 'a' }); trafficStore.set(1, { path: 'b' })
    const [a, b] = [trafficStore.get(0), trafficStore.get(1)]
    trafficStore.set(0, { path: 'c' })
    expect(trafficStore.get(0)).not.toBe(a)
    expect(trafficStore.get(1)).toBe(b)
  })
  it('reset clears', () => {
    trafficStore.set(0, { path: '/a.py' })
    trafficStore.reset()
    expect(trafficStore.all()).toEqual({})
    expect(trafficStore.get(0)).toEqual(DEFAULT_FORM)
  })
  it('subscribe notifies on set and reset, and stops after unsubscribe', () => {
    const fn = vi.fn()
    const off = trafficStore.subscribe(fn)
    trafficStore.set(0, { path: 'x' })
    trafficStore.reset()
    expect(fn).toHaveBeenCalledTimes(2)
    off()
    trafficStore.set(0, { path: 'y' })
    expect(fn).toHaveBeenCalledTimes(2)
  })
  it('reset increments the generation, set does not', () => {
    const g = trafficStore.generation()
    trafficStore.set(0, { path: 'a' })
    expect(trafficStore.generation()).toBe(g)
    trafficStore.reset()
    expect(trafficStore.generation()).toBe(g + 1)
  })
  it('the defaults are frozen', () => {
    expect(Object.isFrozen(DEFAULT_FORM)).toBe(true)
    expect(Object.isFrozen(DEFAULT_FORM.applied)).toBe(true)
  })
})
