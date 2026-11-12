import { afterEach, describe, expect, it, vi } from 'vitest'
import type { PortView } from './api'
import { READINESS_HIDDEN_KEY, readiness, resetReadinessStore, TRAIL, useReadinessStrip } from './readiness'
import { wireProfiles } from './testWire'
import { act, renderHook } from '@testing-library/react'

// Same shape as the local helper in portActions.test.ts.
const port = (id: number, o: Partial<PortView> = {}): PortView => ({
  id, state: 'IDLE', owner: '', owned: false, service: false, service_filtered: false, link_up: true, speed_gbps: 10,
  promiscuous: false, multicast: false, fc_mode: 0, vlan: [], layer_cfg: {}, rx_filter_mode: null, profiles: wireProfiles(o.state), rx_info: {}, info: {}, ...o,
})
const READ_ONLY = 'Read-only session: the port cannot be changed.'

afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); resetReadinessStore() })

describe('readiness', () => {
  it('read-only wins over every other row', () => {
    const ports = [port(0), port(0, { owner: 'bob' }), port(0, { owned: true }), port(0, { owned: true, state: 'STREAMS' }), port(0, { owned: true, state: 'TX' }), port(0, { mine_elsewhere: true, owner: 'me' })]
    for (const p of ports) expect(readiness(p, true)).toEqual({ step: 'blocked', text: READ_ONLY })
  })
  it('an unowned port reads acquire', () => {
    expect(readiness(port(0), false)).toEqual({ step: 'acquire', text: 'Acquire the port, then assign a profile and start.' })
  })
  it('a port owned by another reads blocked and names the owner', () => {
    expect(readiness(port(0, { owner: 'bob' }), false)).toEqual({ step: 'blocked', text: 'Owned by bob: use Force acquire to take it.' })
  })
  it('an owned IDLE port reads assign', () => {
    expect(readiness(port(0, { owned: true, owner: 'me' }), false)).toEqual({ step: 'assign', text: 'Assign a profile on the Traffic tab, then start.' })
  })
  it('an owned port whose default profile is neither STREAMS nor IDLE falls through to assign', () => {
    expect(readiness(port(0, { owned: true, state: 'STREAMS', profiles: { _: 'WEIRD' } }), false).step).toBe('assign')
  })
  it('an owned STREAMS port reads start', () => {
    expect(readiness(port(0, { owned: true, state: 'STREAMS' }), false)).toEqual({ step: 'start', text: 'Start on the Traffic tab.' })
  })
  it('a running port reads running', () => {
    expect(readiness(port(0, { owned: true, state: 'TX' }), false)).toEqual({ step: 'running', text: 'Traffic is running.' })
  })
  it('a DOWN port with a running profile reads running', () => {
    expect(readiness(port(0, { owned: true, state: 'DOWN', link_up: false, profiles: { _: 'TX' } }), false).step).toBe('running')
  })
  it('a DOWN owned port with STREAMS and link down reads the link-down start text', () => {
    expect(readiness(port(0, { owned: true, state: 'DOWN', link_up: false, profiles: { _: 'STREAMS' } }), false))
      .toEqual({ step: 'start', text: 'Link is down: Start on the Traffic tab may be refused by TRex.' })
  })
  it('an unknown link (null) does not read as link down', () => {
    expect(readiness(port(0, { owned: true, state: 'STREAMS', link_up: null }), false).text).toBe('Start on the Traffic tab.')
  })
  it('a port held by another session of yours reads blocked', () => {
    expect(readiness(port(0, { owner: 'me', mine_elsewhere: true }), false)).toEqual({ step: 'blocked', text: 'Held by another session of yours: use Re-acquire my ports.' })
  })
  it('the trail is Acquire, Assign a profile, Start', () => {
    expect(TRAIL).toEqual([{ step: 'acquire', word: 'Acquire' }, { step: 'assign', word: 'Assign a profile' }, { step: 'start', word: 'Start' }])
  })
})

describe('the hide store', () => {
  it('is shown by default and hidden only when the key reads 1', () => {
    expect(renderHook(() => useReadinessStrip()).result.current.shown).toBe(true)
    localStorage.setItem(READINESS_HIDDEN_KEY, 'yes'); resetReadinessStore()
    expect(renderHook(() => useReadinessStrip()).result.current.shown).toBe(true)
    localStorage.setItem(READINESS_HIDDEN_KEY, '1'); resetReadinessStore()
    expect(renderHook(() => useReadinessStrip()).result.current.shown).toBe(false)
  })
  it('setShown(false) writes 1 and setShown(true) removes the key', () => {
    const { result } = renderHook(() => useReadinessStrip())
    act(() => result.current.setShown(false))
    expect(localStorage.getItem(READINESS_HIDDEN_KEY)).toBe('1')
    expect(result.current.shown).toBe(false)
    act(() => result.current.setShown(true))
    expect(localStorage.getItem(READINESS_HIDDEN_KEY)).toBeNull()
    expect(result.current.shown).toBe(true)
  })
  it('storage that throws reads as shown and the choice still lasts for the session', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied') })
    resetReadinessStore()
    const { result } = renderHook(() => useReadinessStrip())
    expect(result.current.shown).toBe(true)
    act(() => result.current.setShown(false))
    expect(result.current.shown).toBe(false)
  })
})
