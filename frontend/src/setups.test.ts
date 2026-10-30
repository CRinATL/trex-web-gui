import { describe, expect, it, vi } from 'vitest'
import { ApiError, type ConnectParams, type Inspected, type PortView, type SetupPort, type TestSetup } from './api'
import { dashboardPrefs } from './dashboardPrefs'
import type { LayerView } from './layerView'
import { PGID_PALETTE } from './pgidPalette'
import { pgidStore } from './pgidStats'
import { formatRate, fromWireMult, type ParsedRate } from './rateText'
import { runInProgress } from './setupSession'
import { buildSetup, fingerprint, formDrift, loadSetup, matchPorts, runSetup as runLoaded, slugify, stableStringify, type RunDeps } from './setups'
import { DEFAULT_FORM, trafficStore, type TrafficForm } from './trafficStore'

const params: ConnectParams = { host: 'h', sync_port: 4501, async_port: 4500, timeout_s: 3 }
const mac = (id: number) => `aa:bb:cc:dd:ee:0${id}`
const view = (id: number, o: Partial<PortView> = {}): PortView => ({
  id, state: 'IDLE', owner: 'me', owned: true, service: false, service_filtered: false, link_up: true, speed_gbps: 10, promiscuous: false,
  multicast: false, fc_mode: 0, vlan: [], layer_cfg: {}, rx_filter_mode: null, profiles: {}, rx_info: {}, info: { hw_mac: mac(id) }, ...o,
})
const ports = [view(0), view(1)]
const insp = (path: string): Inspected => ({
  path, kind: 'py', stream_count: 1, summary: [],
  tunables: [{ dest: 'size', flag: '--size', kind: 'int', default: 64, choices: null, help: '' }],
})
const sp = (port: number, o: Partial<SetupPort> = {}): SetupPort => ({
  key: mac(port), port, profile: { path: `/p/${port}.py`, kind: 'py' }, tunables: { values: { size: 128 }, extra: '' },
  mult: '10kpps', duration: -1, force: false, acquire_force: false, stop_first: false, layer: null, ...o,
})
const setup = (list: SetupPort[], o: Partial<TestSetup> = {}): TestSetup =>
  ({ name: 'Lab A', slug: 'lab-a', created: 'c', updated: 'u1', server: 'h:4501', ports: list, dashboard: null, ...o })
const form = (o: Partial<TrafficForm> = {}): TrafficForm => ({ ...DEFAULT_FORM, formPath: '/p/x.py', path: '/p/x.py', ...o })
const noOpts = { includeLayer: false, stopFirst: false }

// Recording stand-ins for the RPCs: `calls` holds "name ports" in the order they were issued.
function rig(o: { state?: Record<number, string>; profiles?: Record<number, Record<string, string>>; fail?: Partial<Record<string, (b: { ports?: number[]; port?: number; force?: boolean }) => void>>; arp?: 'resolved' | 'unresolved' } = {}) {
  const calls: string[] = []
  const bodies: Record<string, unknown[]> = {}
  let now = ports.map((p) => ({ ...p, ...(o.state?.[p.id] ? { state: o.state[p.id] } : {}), ...(o.profiles?.[p.id] ? { profiles: o.profiles[p.id] } : {}) }))
  const rec = <B extends { ports?: number[]; port?: number }>(name: string, body: B) => {
    calls.push(`${name} ${body.ports ?? body.port}`)
    ;(bodies[name] ??= []).push(body)
    o.fail?.[name]?.(body)
  }
  const deps: Omit<RunDeps, 'current'> = {
    ports: () => now,
    acquire: vi.fn(async (b) => { rec('acquire', b); return { ports: now } }),
    stop: vi.fn(async (b) => { rec('stop', b); now = now.map((p) => (b.ports.includes(p.id) ? { ...p, state: 'STREAMS' } : p)); return { ports: now } }),
    applyLayer: vi.fn(async (b) => { rec('layer', b); return { ports: now, arp: o.arp ? { state: o.arp, mac: null, error: null } : null, service_restore_error: null } }),
    assign: vi.fn(async (b) => { rec('assign', b); return { summary: [], stream_count: 1, rate: {}, ports: now } }),
    start: vi.fn(async (b) => { rec('start', b); return { ports: now } }),
  }
  return { calls, bodies, deps }
}
const okLoad = (s: TestSetup) => ({
  slug: s.slug, updated: s.updated, fingerprint: fingerprint(s), ok: true, serverMismatch: null, generation: trafficStore.generation(),
  ports: s.ports.map((p) => ({ id: p.port, error: null, inspected: insp(p.profile.path) })), forms: {},
})
// runSetup with the list holding `s` itself unless the test gives `current`
const runSetup = (s: TestSetup, load: Parameters<typeof runLoaded>[1], deps: Omit<RunDeps, 'current'> & Partial<Pick<RunDeps, 'current'>>,
  onStep: Parameters<typeof runLoaded>[3], resume?: Parameters<typeof runLoaded>[4]) => runLoaded(s, load, { current: () => [s], ...deps }, onStep, resume)
const refused = (code: string, message = 'refused') => () => { throw new ApiError(409, code, message) }

describe('buildSetup', () => {
  it('buildSetup keys ports as labels do, includes layer only when asked, never sets acquire_force', () => {
    const twin = [view(0, { info: { hw_mac: mac(1) } }), view(1)]    // ports 0 and 1 share a MAC: both are keyed by id
    const forms = { 0: form({ multValue: '10', unit: 'kpps', duration: '' }), 1: form({ multValue: '50', unit: '%', duration: '2.5', force: true }) }
    const layers: Record<number, LayerView> = {
      0: { mode: 'L3', srcMac: mac(0), dstMac: null, srcIpv4: '10.0.0.1', dstIpv4: '10.0.0.2', arp: 'unresolved', vlan: [5], ipv6: 'off' },
      1: { mode: 'L2', srcMac: mac(1), dstMac: '00:11:22:33:44:55', srcIpv4: null, dstIpv4: null, arp: null, vlan: [], ipv6: 'auto' },
    }
    const without = buildSetup(' Lab A ', params, twin, forms, layers, null, new Set([0, 1]), noOpts, 'h:4501')
    expect(without.name).toBe('Lab A')
    expect(without.ports.map((p) => p.key)).toEqual(['#0', '#1'])
    expect(without.ports.map((p) => p.layer)).toEqual([null, null])
    expect(without.ports.map((p) => p.acquire_force)).toEqual([false, false])
    expect(without.ports.map((p) => [p.mult, p.duration, p.force])).toEqual([['10kpps', -1, false], ['50%', 2.5, true]])
    expect(without.dashboard).toBeNull()
    const withLayer = buildSetup('Lab A', params, [view(0), view(1)], forms, layers, null, new Set([0, 1]), { includeLayer: true, stopFirst: true }, null)
    expect(withLayer.ports.map((p) => p.key)).toEqual([mac(0), mac(1)])
    expect(withLayer.ports[0].layer).toEqual({ mode: 'L3', dst_mac: null, src_ipv4: '10.0.0.1', dst_ipv4: '10.0.0.2', vlan: [5], ipv6: { enabled: false, src: null } })
    expect(withLayer.ports[1].layer).toEqual({ mode: 'L2', dst_mac: '00:11:22:33:44:55', src_ipv4: null, dst_ipv4: null, vlan: [], ipv6: { enabled: true, src: null } })
    expect(withLayer.ports.map((p) => [p.stop_first, p.acquire_force])).toEqual([[true, false], [true, false]])
    expect(withLayer.server).toBeNull()
    expect(withLayer.slug).toBe('lab-a')
  })
  it('leaves out ports that are not ticked, have no profile, or have an invalid multiplier or duration', () => {
    const forms = { 0: form(), 1: form({ formPath: null }), 2: form({ multValue: '0' }), 3: form({ duration: '-4' }) }
    const four = [view(0), view(1), view(2), view(3)]
    const s = buildSetup('x', params, four, forms, {}, null, new Set([0, 1, 2, 3]), noOpts, null)
    expect(s.ports.map((p) => p.port)).toEqual([0])
    expect(buildSetup('x', params, four, forms, {}, null, new Set(), noOpts, null).ports).toEqual([])
  })
  it('carries the dashboard section with the backend field names, and only scalar tunable values', () => {
    const forms = { 0: form({ values: { size: 128, name: 'a', on: true, junk: { x: 1 } } }) }
    const dash = { prefs: { tab: 'latency' as const, windowS: 120, layout: 2, yFrom0: false }, streams: [3, 4], latency: [9] }
    const s = buildSetup('x', params, [view(0)], forms, {}, dash, new Set([0]), noOpts, null)
    expect(s.dashboard).toEqual({ tab: 'latency', window_s: 120, layout: 2, streams_pgids: [3, 4], latency_pgids: [9] })
    expect(s.ports[0].tunables.values).toEqual({ size: 128, name: 'a', on: true })
  })
  it('slugify mirrors the server', () => {
    expect(slugify('Lab A: 10 kpps')).toBe('lab-a-10-kpps')
    expect(slugify('!!!')).toBe('')
  })
})

describe('matchPorts', () => {
  it('matchPorts falls back to the port number and reports absence', () => {
    const s = setup([sp(0), sp(1, { key: 'ff:ff:ff:ff:ff:ff' }), sp(7, { key: '#7' })])
    const m = matchPorts(s, params, ports)
    expect(m[0]).toMatchObject({ view: ports[0], reason: null })
    expect(m[1].view).toBe(ports[1])
    expect(m[1].reason).toMatch(/by port number/)
    expect(m[2].view).toBeNull()
    expect(m[2].reason).toBe('not on this server')
  })
})

describe('matchPorts claims', () => {
  it('never gives one server port to two setup ports: the MAC-moved case', async () => {
    // setup: {key A, port 0}, {key B, port 1}. The server's port 0 now has MAC B and port 1 has MAC C.
    const moved = [view(0, { info: { hw_mac: mac(1) } }), view(1, { info: { hw_mac: 'aa:bb:cc:dd:ee:09' } })]
    const s = setup([sp(0, { key: mac(0) }), sp(1, { key: mac(1) })])
    const m = matchPorts(s, params, moved)
    expect(m[1]).toMatchObject({ view: moved[0], reason: null })                 // B is on server port 0, by key
    expect(m[0].view).toBeNull()                                                 // A's number fallback (port 0) is claimed
    expect(m[0].reason).toBe('port 0 is already used by another port of this setup')
    const inspect = vi.fn(async (b: { path: string }) => insp(b.path))
    const r = await loadSetup(s, params, moved, { inspect })
    expect(r.ok).toBe(false)
    expect(trafficStore.all()).toEqual({})
  })
  it('a number fallback still works for a server port nobody claimed by key', () => {
    const m = matchPorts(setup([sp(0, { key: 'ff:ff:ff:ff:ff:ff' }), sp(1)]), params, ports)
    expect(m.map((x) => x.view?.id)).toEqual([0, 1])
  })
})

describe('loadSetup', () => {
  it('Load reports a missing profile per port and leaves the forms unchanged', async () => {
    const s = setup([sp(0), sp(1)], { dashboard: { tab: 'latency', window_s: 120, layout: 2, streams_pgids: [1], latency_pgids: [2] } })
    const inspect = vi.fn(async (b: { path: string }) => {
      if (b.path === '/p/1.py') throw new ApiError(404, 'not_found', '/p/1.py: no such file')
      return insp(b.path)
    })
    const r = await loadSetup(s, params, ports, { inspect })
    expect(r.ok).toBe(false)
    expect(r.ports.map((p) => p.error)).toEqual([null, '/p/1.py: no such file'])
    expect(trafficStore.all()).toEqual({})
    expect(dashboardPrefs.get().tab).toBe('overview')
    expect(pgidStore.selection('streams').picked).toEqual([])
  })
  it('Load reports a setup port the server does not have, and changes nothing', async () => {
    const inspect = vi.fn(async (b: { path: string }) => insp(b.path))
    const r = await loadSetup(setup([sp(0), sp(7, { key: '#7' })]), params, ports, { inspect })
    expect(r.ok).toBe(false)
    expect(r.ports[1]).toEqual({ id: 7, error: 'not on this server', inspected: null })
    expect(trafficStore.all()).toEqual({})
  })
  it('Load fills every form and applies the dashboard section', async () => {
    const s = setup([sp(0, { mult: '50%', duration: 3 }), sp(1, { mult: '2', force: true, tunables: { values: { size: 256 }, extra: '--x 1' } })],
      { dashboard: { tab: 'latency', window_s: 120, layout: 2, streams_pgids: [3, 4], latency_pgids: [9] } })
    const inspect = vi.fn(async (b: { path: string; tunables?: string[] }) => insp(b.path))
    const r = await loadSetup(s, params, ports, { inspect })
    expect(r).toMatchObject({ ok: true, slug: 'lab-a', updated: 'u1', serverMismatch: null })
    // a bare inspect for the specs, then one with the saved values
    expect(inspect.mock.calls.map((c) => c[0])).toEqual([
      { path: '/p/0.py', ports: [0], tunables: [] }, { path: '/p/0.py', ports: [0], tunables: ['--size', '128'] },
      { path: '/p/1.py', ports: [1], tunables: [] }, { path: '/p/1.py', ports: [1], tunables: ['--size', '256', '--x', '1'] },
    ])
    expect(trafficStore.get(0)).toMatchObject({ path: '/p/0.py', formPath: '/p/0.py', multValue: '50', unit: '%', duration: '3', force: false, values: { size: 128 }, extra: '' })
    expect(trafficStore.get(1)).toMatchObject({ multValue: '2', unit: 'x', duration: '', force: true, values: { size: 256 }, extra: '--x 1' })
    expect(trafficStore.get(1).specs).toEqual(insp('/p/1.py').tunables)
    expect(trafficStore.get(1).inspected?.path).toBe('/p/1.py')
    expect(dashboardPrefs.get()).toEqual({ tab: 'latency', windowS: 120, layout: 2, yFrom0: false })
    expect(pgidStore.selection('streams')).toEqual({ picked: [{ id: 3, color: PGID_PALETTE[0] }, { id: 4, color: PGID_PALETTE[1] }], seeded: true })
    expect(pgidStore.selection('latency')).toEqual({ picked: [{ id: 9, color: PGID_PALETTE[0] }], seeded: true })
  })
  it('setup round trip: 2.5gbps loads as 2.5 / gbps and saves back 2.5gbps', async () => {
    const s = setup([sp(0, { mult: '2.5gbps' })])
    const inspect = vi.fn(async (b: { path: string }) => insp(b.path))
    await loadSetup(s, params, ports, { inspect })
    expect(trafficStore.get(0)).toMatchObject({ multValue: '2.5', unit: 'gbps' })
    const back = buildSetup('Lab A', params, [view(0)], { 0: trafficStore.get(0) }, {}, null, new Set([0]), noOpts, null)
    expect(back.ports[0].mult).toBe('2.5gbps')
  })
  it('a setup saved from 14880952 pps stores 14.880952mpps and reloads it exactly, with no drift', async () => {
    const typed = form({ multValue: '14.880952', unit: 'mpps' })   // what the box writes for "14880952 pps"
    const saved = buildSetup('Lab A', params, [view(0)], { 0: typed }, {}, null, new Set([0]), noOpts, null)
    expect(saved.ports[0].mult).toBe('14.880952mpps')
    const inspect = vi.fn(async (b: { path: string }) => insp(b.path))
    const r = await loadSetup(setup([sp(0, { mult: saved.ports[0].mult })]), params, ports, { inspect })
    expect(trafficStore.get(0)).toMatchObject({ multValue: '14.880952', unit: 'mpps' })
    expect(formatRate(fromWireMult('14.880952mpps') as ParsedRate)).toBe('14.880952 Mpps')
    expect(formDrift(r.forms[0], trafficStore.get(0))).toEqual([])
  })
  it('names the server mismatch', async () => {
    const inspect = vi.fn(async (b: { path: string }) => insp(b.path))
    const r = await loadSetup(setup([sp(0)], { server: 'lab-a:4501' }), params, ports, { inspect })
    expect(r.serverMismatch).toMatch(/lab-a:4501/)
    expect(r.serverMismatch).toMatch(/h:4501/)
  })
  it('writes nothing when the connection was reset while it inspected', async () => {
    const inspect = vi.fn(async (b: { path: string }) => { trafficStore.reset(); return insp(b.path) })
    await expect(loadSetup(setup([sp(0)]), params, ports, { inspect })).rejects.toThrow(/connection changed/)
    expect(trafficStore.all()).toEqual({})
  })
})

describe('runSetup', () => {
  const two = setup([sp(0), sp(1)])
  const steps = () => vi.fn()

  it('Run refuses when Load failed and sends no RPC', async () => {
    const r = rig()
    await expect(runSetup(two, { ...okLoad(two), ok: false }, r.deps, steps())).rejects.toThrow(/Load/)
    expect(r.calls).toEqual([])
    expect(runInProgress.get()).toBe(false)
  })
  it('Run refuses a Load of another setup', async () => {
    const r = rig()
    await expect(runSetup({ ...two, slug: 'other' }, okLoad(two), r.deps, steps())).rejects.toThrow(/changed since it was loaded/)
    expect(r.calls).toEqual([])
  })
  it('Run refuses, before any RPC, a list entry with the same slug and updated but another profile path', async () => {
    const r = rig()
    const edited = setup([sp(0, { profile: { path: '/evil.py', kind: 'py' } }), sp(1)])
    expect(edited.updated).toBe(two.updated)
    await expect(runSetup(two, okLoad(two), { ...r.deps, current: () => [edited] }, steps())).rejects.toThrow(/changed since it was loaded; Load it again/)
    expect(r.calls).toEqual([])
    expect(runInProgress.get()).toBe(false)
  })
  it('Run refuses, before any RPC, when the setup is no longer in the list', async () => {
    const r = rig()
    await expect(runSetup(two, okLoad(two), { ...r.deps, current: () => [] }, steps())).rejects.toThrow(/changed since it was loaded/)
    expect(r.calls).toEqual([])
  })
  it('Run refuses cleanly when a port was added to the list entry', async () => {
    const r = rig()
    const wider = setup([sp(0), sp(1), sp(2)])
    await expect(runSetup(two, okLoad(two), { ...r.deps, current: () => [wider] }, steps())).rejects.toThrow(/changed since it was loaded/)
    expect(r.calls).toEqual([])
  })
  it('Run runs the Loaded snapshot when the list entry only changed its timestamps', async () => {
    const r = rig()
    const res = await runSetup(two, okLoad(two), { ...r.deps, current: () => [{ ...two, updated: 'u9', created: 'c9' }] }, steps())
    expect(res.started).toEqual([0, 1])
    expect(r.bodies.assign[0]).toMatchObject({ path: '/p/0.py' })
  })
  it('the fingerprint ignores key order and the timestamps only', () => {
    expect(stableStringify({ b: 1, a: { d: [2, { z: 1, y: 2 }], c: null } })).toBe('{"a":{"c":null,"d":[2,{"y":2,"z":1}]},"b":1}')
    expect(fingerprint(two)).toBe(fingerprint({ ...two, updated: 'x', created: 'y' }))
    expect(fingerprint(two)).not.toBe(fingerprint({ ...two, name: 'Lab B' }))
  })
  it('Run applies every layer before any assign and starts last', async () => {
    const layer = { mode: 'L3' as const, dst_mac: null, src_ipv4: '10.0.0.1', dst_ipv4: '10.0.0.2', vlan: [], ipv6: null }
    const s = setup([sp(0, { layer }), sp(1, { layer: { ...layer, src_ipv4: '10.0.0.2', dst_ipv4: '10.0.0.1' } })])
    const r = rig()
    const res = await runSetup(s, okLoad(s), r.deps, steps())
    expect(r.calls).toEqual(['acquire 0', 'acquire 1', 'layer 0', 'layer 1', 'assign 0', 'assign 1', 'start 0,1'])
    expect(res).toMatchObject({ acquired: [0, 1], started: [0, 1], failed: null, warnings: [] })
    // the layer body is LayerBody: the port, no nulls
    expect(r.bodies.layer[0]).toEqual({ port: 0, mode: 'L3', src_ipv4: '10.0.0.1', dst_ipv4: '10.0.0.2', vlan: [] })
    expect(r.bodies.acquire[0]).toEqual({ ports: [0], force: false })
    expect(r.bodies.assign[0]).toEqual({ ports: [0], path: '/p/0.py', tunables: ['--size', '128'], stop_first: false })
    expect(r.bodies.start[0]).toEqual({ ports: [0, 1], mult: '10kpps', duration: -1, force: false })
    expect(runInProgress.get()).toBe(false)
  })
  it('Run stops stop_first ports that transmit before the layer step', async () => {
    const layer = { mode: 'L2' as const, dst_mac: '00:11:22:33:44:55', src_ipv4: null, dst_ipv4: null, vlan: [], ipv6: null }
    const s = setup([sp(0, { stop_first: true, layer }), sp(1, { stop_first: true })])
    const r = rig({ state: { 0: 'TX' } })     // port 1 is not transmitting, so it is not stopped
    await runSetup(s, okLoad(s), r.deps, steps())
    expect(r.calls).toEqual(['acquire 0', 'acquire 1', 'stop 0', 'layer 0', 'assign 0', 'assign 1', 'start 0,1'])
    expect((r.bodies.assign[0] as { stop_first: boolean }).stop_first).toBe(true)
  })
  it('Run stops a stop_first port whose link is down but whose profile still transmits', async () => {
    const s = setup([sp(0, { stop_first: true }), sp(1, { stop_first: true })])
    const r = rig({ state: { 0: 'DOWN', 1: 'DOWN' }, profiles: { 0: { bench: 'TX' } } })   // port 1 is down and idle: nothing to stop
    await runSetup(s, okLoad(s), r.deps, steps())
    expect(r.calls).toContain('stop 0')
    expect(r.calls).not.toContain('stop 1')
  })
  it('ports with different mult start in one call per group', async () => {
    const s = setup([sp(0), sp(1, { mult: '50%' }), sp(2, { key: '#2' })])
    const three = [...ports, view(2, { info: {} })]
    const r = rig()
    r.deps.ports = () => three
    await runSetup(s, okLoad(s), r.deps, steps())
    expect(r.calls.filter((c) => c.startsWith('start'))).toEqual(['start 0,2', 'start 1'])
    expect(r.bodies.start).toEqual([{ ports: [0, 2], mult: '10kpps', duration: -1, force: false }, { ports: [1], mult: '50%', duration: -1, force: false }])
  })
  it('Run stops at the first failure and lists what started', async () => {
    const s = setup([sp(0), sp(1)])
    const r = rig({ fail: { acquire: (b) => { if (b.ports?.[0] === 1) refused('not_owned', 'owned by bob')() } } })
    const res = await runSetup(s, okLoad(s), r.deps, steps())
    expect(res.acquired).toEqual([0])
    expect(res.started).toEqual([])
    expect(res.failed).toEqual({ id: 1, step: 'acquire', message: 'owned by bob', code: 'not_owned' })
    expect(r.calls).toEqual(['acquire 0', 'acquire 1'])      // nothing was assigned or started

    const g = setup([sp(0), sp(1, { mult: '50%' })])
    const r2 = rig({ fail: { start: (b) => { if (b.ports?.[0] === 1) refused('bad_request', 'no')() } } })
    const res2 = await runSetup(g, okLoad(g), r2.deps, steps())
    expect(res2.started).toEqual([0])
    expect(res2.failed).toMatchObject({ id: 1, step: 'start', code: 'bad_request' })
  })
  it('names the step that failed: a refused layer stops Run before any assign', async () => {
    const layer = { mode: 'L2' as const, dst_mac: '00:11:22:33:44:55', src_ipv4: null, dst_ipv4: null, vlan: [], ipv6: null }
    const s = setup([sp(0, { layer }), sp(1)])
    const r = rig({ fail: { layer: refused('bad_request', 'cannot apply') } })
    const res = await runSetup(s, okLoad(s), r.deps, steps())
    expect(res.failed).toMatchObject({ id: 0, step: 'layer', message: 'cannot apply' })
    expect(r.calls).toEqual(['acquire 0', 'acquire 1', 'layer 0'])
  })
  it('the force-acquire resume skips ports already acquired', async () => {
    const s = setup([sp(0), sp(1), sp(2, { key: '#2' })])
    const r = rig()
    r.deps.ports = () => [...ports, view(2, { info: {} })]
    const res = await runSetup(s, okLoad(s), r.deps, steps(), { acquired: [0], force: 1 })
    expect(r.calls.slice(0, 2)).toEqual(['acquire 1', 'acquire 2'])
    expect(r.bodies.acquire).toEqual([{ ports: [1], force: true }, { ports: [2], force: false }])
    expect(res.acquired).toEqual([0, 1, 2])
    expect(res.started).toEqual([0, 1, 2])
  })
  it('runInProgress is cleared when Run throws', async () => {
    const r = rig()
    const seen: boolean[] = []
    const onStep = () => { seen.push(runInProgress.get()); throw new Error('boom') }
    await expect(runSetup(two, okLoad(two), r.deps, onStep)).rejects.toThrow('boom')
    expect(seen).toEqual([true])
    expect(runInProgress.get()).toBe(false)
  })
  it('a setup port absent on the server stops Run before any RPC', async () => {
    const s = setup([sp(0), sp(7, { key: '#7' })])
    const r = rig()
    const inspect = vi.fn(async (b: { path: string }) => insp(b.path))
    const load = await loadSetup(s, params, ports, { inspect })
    await expect(runSetup(s, load, r.deps, steps())).rejects.toThrow(/Load/)
    // and a port that vanished after a good Load
    const loaded = await loadSetup(two, params, ports, { inspect })
    r.deps.ports = () => [ports[0]]
    await expect(runSetup(two, loaded, r.deps, steps())).rejects.toThrow(/Port 1 is not on this server/)
    expect(r.calls).toEqual([])
  })
  it('an unresolved ARP is a warning not a stop', async () => {
    const layer = { mode: 'L3' as const, dst_mac: null, src_ipv4: '10.0.0.1', dst_ipv4: '10.0.0.2', vlan: [], ipv6: null }
    const s = setup([sp(0, { layer })])
    const r = rig({ arp: 'unresolved' })
    const res = await runSetup(s, okLoad(s), r.deps, steps())
    expect(res.failed).toBeNull()
    expect(res.started).toEqual([0])
    expect(res.warnings).toHaveLength(1)
    expect(res.warnings[0]).toMatch(/Port 0.*10\.0\.0\.2/)
  })
  it('Run warns on a server mismatch and continues', async () => {
    const r = rig()
    const res = await runSetup(two, { ...okLoad(two), serverMismatch: 'saved for lab-a:4501, connected to h:4501' }, r.deps, steps())
    expect(res.warnings).toEqual(['saved for lab-a:4501, connected to h:4501'])
    expect(res.started).toEqual([0, 1])
  })
  it('Run refuses, before any RPC, a Load from before the connection changed', async () => {
    const r = rig()
    const load = okLoad(two)
    trafficStore.reset()
    await expect(runSetup(two, load, r.deps, steps())).rejects.toThrow(/connection changed/)
    expect(r.calls).toEqual([])
    expect(runInProgress.get()).toBe(false)
  })
  it('a reconnect during Run stops it before the next RPC', async () => {
    const r = rig()
    const acquire = r.deps.acquire
    r.deps.acquire = vi.fn(async (b) => { const x = await acquire(b); trafficStore.reset(); return x })
    const res = await runSetup(two, okLoad(two), r.deps, steps())
    expect(r.calls).toEqual(['acquire 0'])
    expect(res.failed).toEqual({ id: 1, step: 'acquire', code: 'connection_changed', message: 'The connection changed during Run; nothing more was sent' })
    expect(res.started).toEqual([])
    expect(runInProgress.get()).toBe(false)
  })
  it('a second Run is refused while one runs', async () => {
    let release: () => void = () => undefined
    const r = rig()
    r.deps.acquire = vi.fn(() => new Promise<{ ports: PortView[] }>((res) => { release = () => res({ ports }) }))
    const notified = vi.fn()
    const unsubscribe = runInProgress.subscribe(notified)
    const first = runSetup(two, okLoad(two), r.deps, steps())
    expect(runInProgress.get()).toBe(true)         // set before the first await
    expect(notified).toHaveBeenCalled()
    await expect(runSetup(two, okLoad(two), r.deps, steps())).rejects.toThrow(/already/)
    expect(r.deps.acquire).toHaveBeenCalledTimes(1)
    release()
    await vi.waitFor(() => expect(r.deps.acquire).toHaveBeenCalledTimes(2))
    release()
    await first
    expect(runInProgress.get()).toBe(false)
    unsubscribe()
  })
})

describe('formDrift', () => {
  const saved = { path: '/a.py', formPath: '/a.py', values: { packet_len: 128, mode: 'fast' }, extra: '', multValue: '10', unit: 'kpps' as const, duration: '', force: false }
  const now = (o: Partial<TrafficForm> = {}): TrafficForm => ({ ...DEFAULT_FORM, ...saved, ...o })
  it('reports nothing for an unchanged form, and for a rate written differently', () => {
    expect(formDrift(saved, now())).toEqual([])
    expect(formDrift(saved, now({ multValue: '10.0' }))).toEqual([])
  })
  it('a change in the seventh significant digit is drift', () => {
    expect(formDrift({ ...saved, multValue: '14.881', unit: 'mpps' }, now({ multValue: '14.880952', unit: 'mpps' })))
      .toEqual(['rate 14.881 Mpps → 14.880952 Mpps'])
  })
  it('names the rate and duration with old and new values', () => {
    expect(formDrift(saved, now({ multValue: '50', duration: '30' }))).toEqual(['rate 10 kpps → 50 kpps', 'duration no limit → 30 s'])
    expect(formDrift({ ...saved, unit: 'x', multValue: '2' }, now({ unit: '%', multValue: '50' }))).toEqual(['rate ×2 → 50%'])
  })
  it('treats a tunable typed back as text as unchanged, but not a boolean flipped', () => {
    expect(formDrift(saved, now({ values: { packet_len: '128', mode: 'fast' } }))).toEqual([])
    expect(formDrift({ ...saved, values: { on: true } }, now({ values: { on: false } }))).toEqual(['on true → false'])
  })
  it('names force, a tunable, extra and the profile path', () => {
    expect(formDrift(saved, now({ force: true }))).toEqual(['force off → on'])
    expect(formDrift(saved, now({ values: { packet_len: 256, mode: 'fast' } }))).toEqual(['packet_len 128 → 256'])
    expect(formDrift(saved, now({ extra: '--x 1' }))).toEqual(['extra none → --x 1'])
    expect(formDrift(saved, now({ formPath: '/b.py', path: '/b.py' }))).toEqual(['profile /a.py → /b.py'])
  })
})

describe('loadSetup forms', () => {
  it('the result carries the forms it wrote, by port id', async () => {
    const s = setup([sp(0, { mult: '50%', duration: 3 }), sp(1, { force: true })])
    const r = await loadSetup(s, params, ports, { inspect: async (b) => insp(b.path) })
    expect(r.forms[0]).toEqual({ path: '/p/0.py', formPath: '/p/0.py', values: { size: 128 }, extra: '', multValue: '50', unit: '%', duration: '3', force: false })
    expect(r.forms[1]).toMatchObject({ multValue: '10', unit: 'kpps', force: true })
  })
  it('Load clears a live-update countdown on a port', async () => {
    trafficStore.set(0, { pending: { mult: '99', left: 3 } })
    expect(trafficStore.get(0).pending).not.toBeNull()
    await loadSetup(setup([sp(0)]), params, ports, { inspect: async (b) => insp(b.path) })
    expect(trafficStore.get(0).pending).toBeNull()
  })
})
