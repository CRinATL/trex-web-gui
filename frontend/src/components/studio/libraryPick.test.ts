import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import type { LibraryEntry } from '../../api'
import { trafficStore } from '../../trafficStore'
import { fillFromJson, fillFromPy, linkedSlug } from './libraryPick'

afterEach(() => { trafficStore.reset(); vi.restoreAllMocks() })

const pyEntry = (): LibraryEntry => ({
  slug: 'b', name: 'B', path: '/lib/b.json', stream_count: 1, created: 'c', updated: 'u', source_missing: false, load_error: null,
  source: { kind: 'py', path: '/srv/udp.py', args: ['--size', '128', '--y', '1'], tunables: { values: { size: '128' }, extra: '--y 1' }, ports_differ: false, imported: 'i' },
})
const SPECS = [{ dest: 'size', flag: '--size', kind: 'int' as const, default: 64, choices: null, help: '' }]

describe('linkedSlug', () => {
  it('linkedSlug holds after fillFromPy and its first Inspect', () => {
    fillFromPy(0, pyEntry(), '/srv/udp.py')
    expect(linkedSlug(trafficStore.get(0))).toBe('b')   // specs unknown: libraryArgs stand in
    trafficStore.set(0, { specs: SPECS })
    expect(linkedSlug(trafficStore.get(0))).toBe('b')   // specs known: tunablesToArgs equals libraryArgs
  })
  it('linkedSlug drops after an argument edit', () => {
    fillFromPy(0, pyEntry(), '/srv/udp.py')
    trafficStore.set(0, { specs: SPECS })
    trafficStore.set(0, { values: { size: '256' } })
    expect(linkedSlug(trafficStore.get(0))).toBeUndefined()
    trafficStore.set(0, { values: { size: '128' } })
    expect(linkedSlug(trafficStore.get(0))).toBe('b')
    trafficStore.set(0, { extra: '--y 2' })
    expect(linkedSlug(trafficStore.get(0))).toBeUndefined()
  })
  it('linkedSlug drops when the path no longer is the pick', () => {
    fillFromPy(0, pyEntry(), '/srv/udp.py')
    trafficStore.set(0, { path: '/srv/other.py' })
    expect(linkedSlug(trafficStore.get(0))).toBeUndefined()
  })
})

describe('fillFromJson', () => {
  it('fillFromJson clears librarySlug', async () => {
    fillFromPy(0, pyEntry(), '/srv/udp.py')
    vi.spyOn(api, 'inspect').mockResolvedValue({ path: '/lib/b.json', name: 'B', kind: 'json', tunables: [], summary: [], stream_count: 1 })
    await fillFromJson(0, { ...pyEntry(), source: null })
    expect(trafficStore.get(0).librarySlug).toBeNull()
  })
})
