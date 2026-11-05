import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { EmuMsg } from './api'
import { EMU_HOSTS_KEY, emuStore, hostLabel, readEmuHosts, rememberEmuHost } from './emuStore'
import * as emuBaseline from './emuBaseline'
import emuMsg from './__fixtures__/emuMsg.json'

const connected = emuMsg.connected as EmuMsg
const disconnected = emuMsg.disconnected as EmuMsg

beforeEach(() => localStorage.clear())
afterEach(() => { vi.restoreAllMocks(); localStorage.clear() })

describe('emuStore', () => {
  it('keeps the latest message and tells its subscribers', () => {
    const seen = vi.fn()
    const off = emuStore.subscribe(seen)
    emuStore.push(connected)
    expect(emuStore.get()).toBe(connected)
    emuStore.push(disconnected)
    expect(emuStore.get()).toBe(disconnected)
    expect(seen).toHaveBeenCalledTimes(2)
    off()
    emuStore.push(connected)
    expect(seen).toHaveBeenCalledTimes(2)
  })
})

describe('counter baselines', () => {
  it('are dropped when the snapshot generation changes, not on a first message or the same generation', () => {
    const clear = vi.spyOn(emuBaseline, 'clearBaselines')
    emuStore.push({ ...connected, generation: 3 })           // whatever came before, the next pushes compare against this
    clear.mockClear()
    const epoch = emuStore.epoch
    emuStore.push({ ...connected, generation: 3 })
    expect(clear).not.toHaveBeenCalled()
    emuStore.push({ ...connected, generation: 4 })
    expect(clear).toHaveBeenCalledTimes(1)
    expect(emuStore.epoch).toBe(epoch + 1)
  })
})

describe('the per-browser target history', () => {
  it('remembers newest first, once each, and caps the list at ten', () => {
    rememberEmuHost({ host: 'a', port: 4510 })
    rememberEmuHost({ host: 'b', port: 4510 })
    expect(rememberEmuHost({ host: 'a', port: 4510 })).toEqual([{ host: 'a', port: 4510 }, { host: 'b', port: 4510 }])
    for (let i = 0; i < 12; i++) rememberEmuHost({ host: `h${i}`, port: 4510 })
    expect(readEmuHosts()).toHaveLength(10)
    expect(readEmuHosts()[0]).toEqual({ host: 'h11', port: 4510 })
  })
  it('drops what is not a host and port pair, and reads garbage as no history', () => {
    localStorage.setItem(EMU_HOSTS_KEY, JSON.stringify([{ host: 'ok', port: 1 }, 'old:1', { host: 3, port: 1 }, { host: 'x', port: 1.5 }]))
    expect(readEmuHosts()).toEqual([{ host: 'ok', port: 1 }])
    localStorage.setItem(EMU_HOSTS_KEY, '{not json')
    expect(readEmuHosts()).toEqual([])
    localStorage.setItem(EMU_HOSTS_KEY, '{}')
    expect(readEmuHosts()).toEqual([])
  })
  it('survives storage that throws: no history, no error', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
    expect(readEmuHosts()).toEqual([])
    expect(rememberEmuHost({ host: 'a', port: 4510 })).toEqual([{ host: 'a', port: 4510 }])
  })
  it('brackets an IPv6 host in its label', () => {
    expect(hostLabel({ host: '::1', port: 4510 })).toBe('[::1]:4510')
    expect(hostLabel({ host: 'lab', port: 4510 })).toBe('lab:4510')
  })
  it('are kept on the first message after page load, whatever its generation', async () => {
    vi.resetModules()
    const fresh = await import('./emuStore')                 // a store that has seen nothing yet
    const base = await import('./emuBaseline')
    const clear = vi.spyOn(base, 'clearBaselines')
    fresh.emuStore.push({ ...connected, generation: 9 })
    expect(clear).not.toHaveBeenCalled()
    expect(fresh.emuStore.epoch).toBe(0)
  })
})
