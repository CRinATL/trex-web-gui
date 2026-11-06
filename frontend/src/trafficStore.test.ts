import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Inspected, LibraryEntry } from './api'
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
  it('reset clears librarySlug', () => {
    trafficStore.set(0, { librarySlug: 'a' })
    trafficStore.reset()
    expect(trafficStore.get(0).librarySlug).toBeNull()
  })
  describe('relink', () => {
    const insp = (path: string, name: string): Inspected => ({ path, name, kind: 'json', tunables: [], summary: [], stream_count: 0 })
    const entry = (slug: string, name: string): LibraryEntry => ({
      slug, name, path: `/lib/${slug}.json`, stream_count: 0, created: 'c', updated: 'u', source: null, source_missing: false, load_error: null,
    })
    it('relink moves forms on the old path and the old slug', () => {
      trafficStore.set(0, { path: '/lib/old.json', formPath: '/lib/old.json', inspected: insp('/lib/old.json', 'Old') })
      trafficStore.set(1, { path: '/s/a.py', libraryPy: '/s/a.py', librarySlug: 'old', inspected: insp('/s/a.py', 'Old') })
      trafficStore.set(2, { path: '/lib/else.json', inspected: insp('/lib/else.json', 'Else') })
      const third = trafficStore.get(2)
      trafficStore.relink({ slug: 'old', path: '/lib/old.json' }, entry('new', 'New'))
      expect(trafficStore.get(0)).toMatchObject({ path: '/lib/new.json', formPath: '/lib/new.json', inspected: { path: '/lib/new.json', name: 'New' } })
      expect(trafficStore.get(1)).toMatchObject({ path: '/s/a.py', librarySlug: 'new', inspected: { path: '/s/a.py', name: 'New' } })
      expect(trafficStore.get(2)).toBe(third)
    })
    it('relink with the same path refreshes inspected.name', () => {
      trafficStore.set(0, { path: '/lib/a.json', inspected: insp('/lib/a.json', 'cdm test 1') })
      trafficStore.relink({ slug: 'a', path: '/lib/a.json' }, entry('a', 'Cdm Test 1'))
      expect(trafficStore.get(0).inspected?.name).toBe('Cdm Test 1')
      expect(trafficStore.get(0).path).toBe('/lib/a.json')
    })
  })
  it('the defaults are frozen', () => {
    expect(Object.isFrozen(DEFAULT_FORM)).toBe(true)
    expect(Object.isFrozen(DEFAULT_FORM.applied)).toBe(true)
  })
})
