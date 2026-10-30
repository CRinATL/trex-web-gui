import { describe, expect, it, vi } from 'vitest'
import * as api from './api'
import type { PortView } from './api'
import { wirePort, wireProfiles } from './testWire'
import { initialState } from './state'
import { actionsFor, isRunning, resetCaptureWarning, type Selection } from './portActions'

const port = (id: number, o: Partial<PortView> = {}): PortView => ({
  id, state: 'IDLE', owner: '', owned: false, service: false, service_filtered: false, link_up: true, speed_gbps: 10,
  promiscuous: false, multicast: false, fc_mode: 0, vlan: [], layer_cfg: {}, rx_filter_mode: null, profiles: wireProfiles(o.state), rx_info: {}, info: {}, ...o,
})
const enabledMap = (sel: Selection, ports: PortView[], ro = false) => Object.fromEntries(actionsFor(sel, ports, ro, true).map((a) => [a.id, a.enabled]))
const sel0: Selection = { kind: 'port', id: 0 }
const ALL_OFF = { acquire: false, force_acquire: false, release: false, service: false, assign: false, start: false, stop: false, pause: false, resume: false, update: false, reset: false, rate: false, name: true }

describe('service action', () => {
  const svc = (p: PortView) => actionsFor({ kind: 'port', id: 0 }, [p], false, true).find((a) => a.id === 'service')!
  it('offers Enable only when neither on nor filtered, and Disable otherwise', () => {
    expect(svc(port(0, { owned: true })).label).toBe('Enable service mode')
    expect(svc(port(0, { owned: true, service: true })).label).toBe('Disable service mode')
    expect(svc(port(0, { owned: true, service_filtered: true })).label).toBe('Disable service mode')
  })
  it('on a filtered port Disable sends enabled false', async () => {
    const spy = vi.spyOn(api, 'setService').mockResolvedValue({ ports: [] })
    await svc(port(0, { owned: true, service_filtered: true })).run()
    expect(spy).toHaveBeenCalledWith({ ports: [0], enabled: false })
    spy.mockRestore()
  })
})

describe('isRunning', () => {
  it.each(['TX', 'PCAP_TX', 'PAUSE'])('a port in %s runs', (st) => expect(isRunning(port(0, { state: st }))).toBe(true))
  it.each(['IDLE', 'STREAMS'])('a port in %s does not', (st) => expect(isRunning(port(0, { state: st }))).toBe(false))
  it('a DOWN port runs when any profile, a foreign one too, is transmitting or paused', () => {
    expect(isRunning(port(0, { state: 'DOWN', link_up: false, profiles: { _: 'TX' } }))).toBe(true)
    expect(isRunning(port(0, { state: 'DOWN', link_up: false, profiles: { bench: 'PAUSE' } }))).toBe(true)
    expect(isRunning(port(0, { state: 'DOWN', link_up: false, profiles: { _: 'STREAMS', bench: 'TX' } }))).toBe(true)
  })
  it('a DOWN port with no running profile does not', () => {
    expect(isRunning(port(0, { state: 'DOWN', link_up: false }))).toBe(false)
    expect(isRunning(port(0, { state: 'DOWN', link_up: false, profiles: { _: 'STREAMS' } }))).toBe(false)
  })
})

describe('actionsFor', () => {
  it('a transmitting port that lost link keeps Stop and stays in Stop all', async () => {
    const down = port(0, { owned: true, state: 'DOWN', link_up: false, profiles: { _: 'TX' } })
    expect(enabledMap(sel0, [down]).stop).toBe(true)
    expect(enabledMap({ kind: 'root' }, [down]).stop_all).toBe(true)
    const spy = vi.spyOn(api, 'stop').mockResolvedValue({ ports: [] })
    await actionsFor({ kind: 'root' }, [down, port(1, { owned: true, state: 'DOWN', link_up: false })], false, true).find((a) => a.id === 'stop_all')!.run()
    expect(spy).toHaveBeenCalledWith({ ports: [0] })
    spy.mockRestore()
  })
  it('with only a named profile running, stop is enabled and start, pause, resume and update are not', () => {
    const m = enabledMap(sel0, [wirePort('foreign_bench_tx')])
    expect(m.stop).toBe(true)
    expect([m.start, m.pause, m.resume, m.update]).toEqual([false, false, false, false])
  })
  // Real wire shape: `_` is absent while idle (ports.py _drop_idle_profiles), so the aggregate state comes from `bench`.
  it.each([['TX'], ['PAUSE']])('a foreign profile alone in %s leaves Start, Pause, Resume and Update disabled', (st) => {
    const m = enabledMap(sel0, [port(0, { owned: true, state: st, profiles: { bench: st } })])
    expect(m.stop).toBe(true)
    expect([m.start, m.pause, m.resume, m.update]).toEqual([false, false, false, false])
  })
  it('a foreign profile alone in STREAMS leaves Start and root Start all disabled', () => {
    const ports = [port(0, { owned: true, state: 'STREAMS', profiles: { bench: 'STREAMS' } })]
    expect(enabledMap(sel0, ports).start).toBe(false)
    expect(enabledMap({ kind: 'root' }, ports).start_all).toBe(false)
  })
  it('stop_all stops only the running owned ports', async () => {
    const spy = vi.spyOn(api, 'stop').mockResolvedValue({ ports: [] })
    const ports = [port(0, { owned: true, state: 'TX' }), port(1, { owned: true, state: 'STREAMS' }), port(2, { owned: false, state: 'TX' }), port(3, { owned: true, state: 'PAUSE' })]
    await actionsFor({ kind: 'root' }, ports, false, true).find((a) => a.id === 'stop_all')!.run()
    expect(spy).toHaveBeenCalledWith({ ports: [0, 3] })
    spy.mockRestore()
  })
  it('a session that is not connected (LOST) disables every action, whatever the stale ports say', () => {
    for (const s of [sel0, { kind: 'root' } as Selection]) {
      const acts = actionsFor(s, [port(0, { owned: true, state: 'TX' })], false, false)
      expect(acts.length).toBeGreaterThan(0)
      expect(acts.filter((a) => a.id !== 'name').every((a) => !a.enabled)).toBe(true)
    }
  })
  it('Show rate: owned, writable, connected and not IDLE/DOWN only', () => {
    const rate = (p: PortView, ro = false, connected = true) => actionsFor(sel0, [p], ro, connected).find((a) => a.id === 'rate')!
    expect(rate(port(0, { owned: true, state: 'STREAMS' })).enabled).toBe(true)
    expect(rate(port(0, { owned: true, state: 'TX' })).enabled).toBe(true)
    expect(rate(port(0, { owned: true, state: 'IDLE' })).enabled).toBe(false)
    expect(rate(port(0, { owned: true, state: 'DOWN' })).enabled).toBe(false)
    expect(rate(port(0, { state: 'STREAMS', owner: 'bob' })).enabled).toBe(false)
    expect(rate(port(0, { owned: true, state: 'STREAMS' }), true).enabled).toBe(false)
    expect(rate(port(0, { owned: true, state: 'STREAMS' }), false, false).enabled).toBe(false)
    expect(rate(port(0, { owned: true, state: 'STREAMS' })).panelOnly).toBe(true)
  })
  it('read-only disables every action', () => {
    for (const s of [sel0, { kind: 'root' } as Selection]) {
      const acts = actionsFor(s, [port(0, { owned: true, state: 'TX' })], true, true)
      expect(acts.length).toBeGreaterThan(0)
      expect(acts.filter((a) => a.id !== 'name').every((a) => !a.enabled)).toBe(true)
    }
  })
  it('a port that is not owned can only be acquired, force-acquired or reset', () => {
    expect(enabledMap(sel0, [port(0, { owner: 'bob' })])).toEqual({ ...ALL_OFF, acquire: true, force_acquire: true, reset: true })
  })
  it('owned and IDLE: Start disabled with the hint', () => {
    const ports = [port(0, { owned: true, state: 'IDLE' })]
    expect(enabledMap(sel0, ports)).toEqual({ ...ALL_OFF, release: true, service: true, assign: true, reset: true, force_acquire: true })
    expect(actionsFor(sel0, ports, false, true).find((a) => a.id === 'start')!.label).toContain('assign a profile first')
  })
  it('owned and STREAMS: Start enabled, Pause and Resume disabled', () => {
    expect(enabledMap(sel0, [port(0, { owned: true, state: 'STREAMS' })])).toEqual({ ...ALL_OFF, release: true, service: true, assign: true, start: true, rate: true, reset: true, force_acquire: true })
  })
  it('owned and TX: Stop, Pause and Update enabled, Start disabled', () => {
    expect(enabledMap(sel0, [port(0, { owned: true, state: 'TX' })])).toEqual({ ...ALL_OFF, release: true, service: true, assign: true, stop: true, pause: true, update: true, rate: true, reset: true, force_acquire: true })
  })
  it('owned and PAUSE: Resume and Stop enabled', () => {
    expect(enabledMap(sel0, [port(0, { owned: true, state: 'PAUSE' })])).toEqual({ ...ALL_OFF, release: true, service: true, assign: true, stop: true, resume: true, rate: true, reset: true, force_acquire: true })
  })
  it('root: Release all needs an owned port, Stop all an owned TX or PAUSE port', () => {
    const root: Selection = { kind: 'root' }
    const base = { acquire_all: true, force_acquire_all: true, reacquire: false }
    expect(enabledMap(root, [port(0), port(1, { state: 'TX', owner: 'bob' })])).toEqual({ ...base, release_all: false, start_all: false, stop_all: false })
    expect(enabledMap(root, [port(0, { owned: true }), port(1)])).toEqual({ ...base, release_all: true, start_all: false, stop_all: false })
    expect(enabledMap(root, [port(0, { owned: true, state: 'PAUSE' })])).toEqual({ ...base, release_all: true, start_all: false, stop_all: true })
    expect(actionsFor(root, [port(0)], false, true).map((a) => a.label)).toEqual(['Acquire all', 'Force acquire all', 'Release all', 'Re-acquire my ports', 'Start all', 'Stop all'])
  })
  it('root: Re-acquire my ports needs a port another session of this user holds (RC1-25)', () => {
    const root: Selection = { kind: 'root' }
    expect(enabledMap(root, [port(0, { owner: 'me', mine_elsewhere: true }), port(1)]).reacquire).toBe(true)
    expect(enabledMap(root, [port(0, { owner: 'bob' })]).reacquire).toBe(false)
  })
  it('root Start all needs an owned STREAMS port, is a request, and is disabled read-only', () => {
    const root: Selection = { kind: 'root' }
    const request = vi.fn()
    const find = (ports: PortView[], ro = false) => actionsFor(root, ports, ro, true, { request }).find((a) => a.id === 'start_all')!
    expect(find([port(0, { owned: true, state: 'STREAMS' })]).enabled).toBe(true)
    expect(find([port(0, { state: 'STREAMS', owner: 'bob' })]).enabled).toBe(false)
    expect(find([port(0, { owned: true, state: 'STREAMS' })], true).enabled).toBe(false)
    const a = find([port(0, { owned: true, state: 'STREAMS' })])
    expect(a.request).toBe(true)
    void a.run()
    expect(request).toHaveBeenCalledWith('start_all', null)
  })
  it('a profile node other than "_" is read-only with the multi-profile hint; "_" behaves like the port', () => {
    const ports = [port(0, { owned: true, state: 'TX' })]
    const acts = actionsFor({ kind: 'profile', port: 0, profile: 'p2' }, ports, false, true)
    expect(acts.filter((a) => a.id !== 'name').every((a) => !a.enabled && a.label.includes('read-only (multi-profile, P8)'))).toBe(true)
    expect(enabledMap({ kind: 'profile', port: 0, profile: '_' }, ports)).toEqual(enabledMap(sel0, ports))
  })
  it('Name… is enabled in read-only, on a foreign port, while LOST and on a secondary profile, and asks the page to open the dialog', () => {
    const request = vi.fn()
    const name = (sel: Selection, ports: PortView[], ro: boolean, connected: boolean) => actionsFor(sel, ports, ro, connected, { request }).find((a) => a.id === 'name')!
    expect(name(sel0, [port(0, { owned: true, state: 'TX' })], true, true).enabled).toBe(true)
    expect(name(sel0, [port(0, { owner: 'bob' })], false, true).enabled).toBe(true)
    expect(name(sel0, [port(0, { owner: 'bob' })], true, false).enabled).toBe(true)
    const sec = name({ kind: 'profile', port: 0, profile: 'p2' }, [port(0, { owned: true })], false, true)
    expect(sec.enabled).toBe(true)
    expect(sec.label).toBe('Name…')
    expect(sec.request).toBe(true)
    void sec.run()
    expect(request).toHaveBeenCalledWith('name', 0)
  })
  it('the root menu has no Name…', () => {
    expect(actionsFor({ kind: 'root' }, [port(0)], false, true).some((a) => a.id === 'name')).toBe(false)
  })
  it('Reset is danger and enabled on a foreign-owned port', () => {
    const reset = actionsFor(sel0, [port(0, { owner: 'bob' })], false, true).find((a) => a.id === 'reset')!
    expect(reset.danger).toBe(true)
    expect(reset.enabled).toBe(true)
  })
})

describe('Reset confirmation text', () => {
  const cap = (id: number, count: number) => ({ id, count } as import('./api').CaptureRow)
  const msg = (captures: import('./api').CaptureRow[]) => ({ ...initialState.capture, captures })
  it('names every capture id, with singular forms', () => {
    expect(resetCaptureWarning(msg([cap(3, 4), cap(5, 0), cap(7, 2)]))).toBe('Captures 3, 5, 7 will be removed from the server (6 unsaved packets)')
    expect(resetCaptureWarning(msg([cap(3, 1)]))).toBe('Capture 3 will be removed from the server (1 unsaved packet)')
    expect(resetCaptureWarning(msg([]))).toBeNull()
  })
  it('the context-menu Reset carries it as its confirm text, and only when captures exist', () => {
    const find = (capture: import('./api').CaptureMsg) =>
      actionsFor({ kind: 'port', id: 0 }, [port(0, { owner: 'bob' })], false, true, { capture }).find((a) => a.id === 'reset')!
    expect(find(msg([cap(3, 4)])).confirm).toBe('Capture 3 will be removed from the server (4 unsaved packets)')
    expect(find(msg([])).confirm).toBeUndefined()
  })
})
