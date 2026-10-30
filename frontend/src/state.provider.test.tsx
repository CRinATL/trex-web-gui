import { useEffect } from 'react'
import { act, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from './api'
import { AppStateProvider, useAppState, useBusy, usePingLog, usePingRunner } from './state'
import { captureStore } from './captureStore'
import { clearAll } from './clearStats'
import { DEFAULT_INTERVAL, DEFAULT_LAYOUT } from './chartSeries'
import { dashboardPrefs } from './dashboardPrefs'
import { pgidStore } from './pgidStats'
import { statsStore } from './stats'
import { DEFAULT_FORM, trafficStore } from './trafficStore'
import { statsFromSample } from './testStats'
import { wirePgid, wireUtil } from './testPgid'
import { wirePing } from './testWire'
import capturePkts from './__fixtures__/capturePkts.json'
import { utilStore } from './utilStats'
import { xstatsStore } from './xstatsStore'
import * as ws from './ws'

function Show() { const s = useAppState(); return <span data-testid="st">{s.session.state}</span> }

afterEach(() => { vi.restoreAllMocks() })

describe('AppStateProvider', () => {
  it('trusts the server-pushed snapshot: no GET on socket open, so a stale reply cannot overwrite a newer push', async () => {
    // The backend sends hello then the session snapshot on every WebSocket accept (api/app.py ws handler).
    let resolveGet: (v: api.SessionSnapshot) => void = () => undefined
    const get = vi.spyOn(api, 'getSession').mockReturnValue(new Promise((r) => { resolveGet = r }))
    let onMsg: (m: unknown) => void = () => undefined
    let onStatus: (open: boolean) => void = () => undefined
    vi.spyOn(ws, 'connectSocket').mockImplementation((_url, m, s) => { onMsg = m; onStatus = s; return () => undefined })
    render(<AppStateProvider><Show /></AppStateProvider>)
    act(() => onStatus(true))
    act(() => onMsg({ type: 'session', state: 'connected', params: null, info: null, error: null, rpc_console: false, include_polling: false }))
    // a stale GET reply, if one were issued, would arrive now and say "disconnected"
    await act(async () => { resolveGet({ type: 'session', state: 'disconnected', params: null, info: null, error: null, rpc_console: false, include_polling: false }); await Promise.resolve() })
    expect(get).not.toHaveBeenCalled()
    expect(screen.getByTestId('st')).toHaveTextContent('connected')
  })

  it('routes stats to the store, leaving state unchanged with no re-render; session messages tell the store', () => {
    let onMsg: (m: unknown) => void = () => undefined
    vi.spyOn(ws, 'connectSocket').mockImplementation((_url, m) => { onMsg = m; return () => undefined })
    const seen: unknown[] = []
    function Probe() { const s = useAppState(); seen.push(s); return null }
    render(<AppStateProvider><Probe /></AppStateProvider>)
    const session = (state: string) => ({ type: 'session', state, params: null, info: null, error: null, rpc_console: false, include_polling: false })
    act(() => onMsg(session('connected')))
    const renders = seen.length
    const before = seen[renders - 1]
    act(() => onMsg({ type: 'stats', ts: 5, interval_s: 1, global: { cpu_util: 1 }, ports: {}, total: {} }))
    expect(statsStore.latest()?.ts).toBe(5)
    expect(seen.length).toBe(renders) // no re-render: state is unchanged
    expect(seen[seen.length - 1]).toBe(before)
    act(() => onMsg(session('connecting')))
    expect(statsStore.latest()).toBeNull()
  })

  const snap = (state: string, connect_seq: string) => ({ type: 'session', state, params: null, info: null, error: null, rpc_console: false, include_polling: false, connect_seq })

  it('routes pgid and util to their stores, leaving state unchanged with no re-render', () => {
    let onMsg: (m: unknown) => void = () => undefined
    vi.spyOn(ws, 'connectSocket').mockImplementation((_url, m) => { onMsg = m; return () => undefined })
    const seen: unknown[] = []
    function Probe() { const s = useAppState(); seen.push(s); return null }
    render(<AppStateProvider><Probe /></AppStateProvider>)
    act(() => onMsg(snap('connected', 'r:1')))
    const renders = seen.length
    act(() => onMsg({ ...wirePgid('tx'), ts: 6 }))
    act(() => onMsg({ ...wireUtil('tx'), ts: 6 }))
    expect(pgidStore.latest()?.ts).toBe(6)
    expect(utilStore.latest()?.ts).toBe(6)
    expect(seen.length).toBe(renders)
  })

  it('capture_pkts goes to captureStore, not the reducer', () => {
    let onMsg: (m: unknown) => void = () => undefined
    vi.spyOn(ws, 'connectSocket').mockImplementation((_url, m) => { onMsg = m; return () => undefined })
    const seen: unknown[] = []
    function Probe() { const s = useAppState(); seen.push(s); return null }
    render(<AppStateProvider><Probe /></AppStateProvider>)
    act(() => onMsg(snap('connected', 'c:1')))
    const renders = seen.length
    act(() => onMsg(capturePkts.batch))
    expect(captureStore.rows()).toHaveLength(capturePkts.batch.rows.length)
    expect(seen.length).toBe(renders)
  })

  it('a connect_seq change resets captureStore', () => {
    let onMsg: (m: unknown) => void = () => undefined
    vi.spyOn(ws, 'connectSocket').mockImplementation((_url, m) => { onMsg = m; return () => undefined })
    render(<AppStateProvider><Show /></AppStateProvider>)
    act(() => onMsg(snap('connected', 'c:2')))
    act(() => onMsg(capturePkts.batch))
    act(() => onMsg(snap('connected', 'c:2')))   // the same seq again: kept
    expect(captureStore.rows().length).toBeGreaterThan(0)
    act(() => onMsg(snap('connected', 'c:3')))
    expect(captureStore.rows()).toEqual([])
  })

  it('a stats_epoch change drops every port baseline', () => {
    // Two providers on one fake socket (they share the module-level stores): a Reset sent through one rebases both views.
    const handlers: ((m: unknown) => void)[] = []
    vi.spyOn(ws, 'connectSocket').mockImplementation((_url, m) => { handlers.push(m); return () => undefined })
    render(<><AppStateProvider><Show /></AppStateProvider><AppStateProvider><Show /></AppStateProvider></>)
    expect(handlers).toHaveLength(2)
    const stats = (i: number, epoch: number, opackets: number) => ({ ...statsFromSample(i, { ts: opackets, ports: { '0': { opackets } } }), stats_epoch: epoch })
    act(() => handlers[0](snap('connected', 'e:1')))
    act(() => handlers[0](stats(3, 1, 1000)))
    clearAll()
    act(() => handlers[1](stats(3, 1, 1010)))
    expect(statsStore.relative('0', 'opackets')).toBe(10)
    act(() => handlers[0](stats(3, 2, 1500)))   // the other browser's Reset: counters zeroed and regrown past the baseline
    expect(statsStore.relative('0', 'opackets')).toBe(1500)
  })

  it('a connect_seq change resets the reducer and the stores, while an unchanged one after a WebSocket reconnect keeps a Clear baseline', () => {
    let onMsg: (m: unknown) => void = () => undefined
    let onStatus: (open: boolean) => void = () => undefined
    vi.spyOn(ws, 'connectSocket').mockImplementation((_url, m, s) => { onMsg = m; onStatus = s; return () => undefined })
    function Detail() {
      const s = useAppState()
      return <span data-testid="d">{`${s.backendOnline}|${s.serverHint}|${s.ports.length}|${s.log.length}|${s.session.connect_seq}|${Object.keys(s.portLabels).join()}|${s.portLabelsError}`}</span>
    }
    render(<AppStateProvider><Detail /></AppStateProvider>)
    const text = () => screen.getByTestId('d').textContent
    act(() => onStatus(true))
    act(() => onMsg({ type: 'hello', app_version: '1', server_hint: 'lab9', server_ports: null }))
    act(() => onMsg(snap('connected', 'c:1')))
    act(() => onMsg({ type: 'port_labels', labels: { 'h:1': { '#0': { name: 'a', description: '' } } }, load_error: 'oops' }))
    act(() => onMsg({ type: 'ports', ports: [{ id: 0 }] }))
    act(() => onMsg({ type: 'log', level: 'info', message: 'x', ts: 1 }))
    act(() => onMsg(statsFromSample(3)))
    act(() => onMsg({ ...wirePgid('tx'), ts: 1 }))
    act(() => onMsg({ ...wireUtil('tx'), ts: 1 }))
    act(() => xstatsStore.push({ port: 0, values: { a: 5 } }))
    expect(text()).toBe('true|lab9|1|2|c:1|h:1|oops')   // the Connected mark and x
    clearAll()
    expect(pgidStore.relative(5, 'tx_pkts', 'total')).toBe(0)

    // the page's WebSocket drops and reconnects: the backend repeats the same snapshot
    act(() => onStatus(false))
    act(() => onStatus(true))
    act(() => onMsg(snap('connected', 'c:1')))
    expect(pgidStore.relative(5, 'tx_pkts', 'total')).toBe(0)
    expect(pgidStore.latest()).not.toBeNull()
    expect(text()).toBe('true|lab9|1|2|c:1|h:1|oops')
    expect(xstatsStore.names(0)).toEqual(['a'])

    // a new connect: the reducer and all three stores start over; the backend socket state and the server hint stay
    act(() => onMsg(snap('connected', 'c:2')))
    // the log is kept (the x entry plus the marks); the ports and the stores start over
    expect(text()).toBe('true|lab9|0|3|c:2|h:1|oops')
    expect(statsStore.latest()).toBeNull()
    expect(pgidStore.latest()).toBeNull()
    expect(utilStore.latest()).toBeNull()
    expect(xstatsStore.names(0)).toEqual([])
    act(() => onMsg({ ...wirePgid('tx'), ts: 2 }))
    expect(pgidStore.relative(5, 'tx_pkts', 'total')).toBe(90909)
  })
  it('a connect_seq change resets trafficStore and dashboardPrefs; the same connect_seq again keeps them', () => {
    let onMsg: (m: unknown) => void = () => undefined
    vi.spyOn(ws, 'connectSocket').mockImplementation((_url, m) => { onMsg = m; return () => undefined })
    render(<AppStateProvider><Show /></AppStateProvider>)
    act(() => onMsg(snap('connected', 'f:1')))
    trafficStore.set(2, { path: '/p/a.py', multValue: '9' })
    dashboardPrefs.set({ tab: 'latency', windowS: 300, layout: 4 })
    act(() => onMsg(snap('connected', 'f:1')))
    expect(trafficStore.get(2).path).toBe('/p/a.py')
    expect(dashboardPrefs.get().tab).toBe('latency')
    act(() => onMsg(snap('connected', 'f:2')))
    expect(trafficStore.all()).toEqual({})
    expect(trafficStore.get(2)).toEqual(DEFAULT_FORM)
    expect(dashboardPrefs.get()).toEqual({ tab: 'overview', windowS: DEFAULT_INTERVAL, layout: DEFAULT_LAYOUT, yFrom0: false })
  })
})

describe('ping log and busy', () => {
  const snapshot = (connect_seq: string) => ({ type: 'session', state: 'connected', params: null, info: null, error: null, rpc_console: false, include_polling: false, connect_seq })
  let onMsg: (m: unknown) => void = () => undefined
  const mount = (ui: React.ReactNode) => {
    vi.spyOn(ws, 'connectSocket').mockImplementation((_url, m) => { onMsg = m; return () => undefined })
    return render(<AppStateProvider>{ui}</AppStateProvider>)
  }
  const texts = (p: number) => `log${p}`
  function Log({ port }: { port: number }) { return <span data-testid={texts(port)}>{usePingLog(port).map((l) => `${l.seq}:${l.text}`).join('|')}</span> }

  it('ping messages land in their port\'s log and survive a page change', () => {
    function Page({ show }: { show: boolean }) { return show ? <Log port={0} /> : null }
    function Both() { return <><Page show /><Log port={1} /></> }
    const { rerender } = mount(<Both />)
    act(() => onMsg(snapshot('p:1')))
    act(() => onMsg(wirePing('reply', { seq: 1, count: 2 })))
    act(() => onMsg(wirePing('timeout', { seq: 2, count: 2 })))
    act(() => onMsg(wirePing('reply_v6', { port: 1 })))
    expect(screen.getByTestId(texts(0)).textContent).toBe(`1:${wirePing('reply').text}|2:${wirePing('timeout').text}`)
    expect(screen.getByTestId(texts(1)).textContent).toBe(`1:${wirePing('reply_v6').text}`)
    // the page is left and entered again: the provider (and the log) stay
    rerender(<AppStateProvider><Page show={false} /></AppStateProvider>)
    rerender(<AppStateProvider><Both /></AppStateProvider>)
    expect(screen.getByTestId(texts(0)).textContent).toBe(`1:${wirePing('reply').text}|2:${wirePing('timeout').text}`)
  })

  it('a new run (seq 1 again) is appended after the previous one', () => {
    mount(<Log port={0} />)
    act(() => onMsg(snapshot('p:1')))
    act(() => onMsg(wirePing('reply', { seq: 1, count: 1 })))
    act(() => onMsg(wirePing('timeout', { seq: 1, count: 1 })))
    expect(screen.getByTestId(texts(0)).textContent).toBe(`1:${wirePing('reply').text}|1:${wirePing('timeout').text}`)
  })

  const ping = (run: number, seq: number, text: string, over: Partial<ReturnType<typeof wirePing>> = {}) => wirePing('reply', { run, seq, count: 3, text, ...over })
  const replyFor = (run: number) => ({ run, lines: [1, 2, 3].map((n) => ({ status: n === 2 ? 'timeout' as const : 'reply' as const, text: `r${run}.${n}`, rtt_ms: n === 2 ? null : 1 })), service_restore_error: null })
  function Runner({ onRun }: { onRun: (r: ReturnType<typeof usePingRunner>) => void }) { const run = usePingRunner(); useEffect(() => { onRun(run) }); return null }
  const runnerBox = () => {
    const box: { run: ReturnType<typeof usePingRunner> } = { run: () => Promise.reject(new Error('unmounted')) }
    return { box, onRun: (r: ReturnType<typeof usePingRunner>) => { box.run = r } }
  }

  it('the POST response fills a missing streamed line, in place, and keeps an earlier run', async () => {
    vi.spyOn(api, 'ping').mockResolvedValue(replyFor(2))
    const { box, onRun } = runnerBox()
    mount(<><Runner onRun={onRun} /><Log port={0} /></>)
    act(() => onMsg(snapshot('p:1')))
    act(() => onMsg(ping(1, 1, 'earlier')))
    act(() => onMsg(ping(2, 1, 'r2.1', { ts: 7 })))
    act(() => onMsg(ping(2, 3, 'r2.3')))   // seq 2 never arrived
    await act(async () => { await box.run(0, '2.2.2.2', 3, 64) })
    expect(screen.getByTestId(texts(0)).textContent).toBe('1:earlier|1:r2.1|2:r2.2|3:r2.3')
    expect(api.ping).toHaveBeenCalledWith({ port: 0, dst: '2.2.2.2', count: 3, pkt_size: 64 })
  })

  it('a streamed line arriving after the response is dropped, not duplicated', async () => {
    vi.spyOn(api, 'ping').mockResolvedValue(replyFor(2))
    const { box, onRun } = runnerBox()
    mount(<><Runner onRun={onRun} /><Log port={0} /></>)
    act(() => onMsg(snapshot('p:1')))
    act(() => onMsg(ping(2, 1, 'r2.1')))
    await act(async () => { await box.run(0, '2.2.2.2', 3, 64) })
    act(() => onMsg(ping(2, 3, 'r2.3')))   // the hub delivered it after the HTTP response
    expect(screen.getByTestId(texts(0)).textContent).toBe('1:r2.1|2:r2.2|3:r2.3')
    act(() => onMsg(ping(3, 1, 'next run')))   // the next run is not dropped
    expect(screen.getByTestId(texts(0)).textContent).toBe('1:r2.1|2:r2.2|3:r2.3|1:next run')
  })

  it('a response with no streamed line appends the run', async () => {
    vi.spyOn(api, 'ping').mockResolvedValue(replyFor(5))
    const { box, onRun } = runnerBox()
    mount(<><Runner onRun={onRun} /><Log port={0} /></>)
    act(() => onMsg(snapshot('p:1')))
    await act(async () => { await box.run(0, '2.2.2.2', 3, 64) })
    expect(screen.getByTestId(texts(0)).textContent).toBe('1:r5.1|2:r5.2|3:r5.3')
  })

  it('the ping log is capped per port, also for a tab that only watches', () => {
    mount(<Log port={0} />)
    act(() => onMsg(snapshot('p:1')))
    for (let i = 1; i <= 205; i++) act(() => onMsg(ping(1, i, `t${i}`)))
    const lines = screen.getByTestId(texts(0)).textContent!.split('|')
    expect(lines).toHaveLength(200)
    expect(lines[0]).toBe('6:t6')
    expect(lines[199]).toBe('205:t205')
  })

  it('a connect_seq change clears the ping log and the busy map; the same one repeated keeps them', () => {
    function Busy() { return <span data-testid="busy">{JSON.stringify(useBusy())}</span> }
    mount(<><Log port={0} /><Busy /></>)
    act(() => onMsg(snapshot('p:1')))
    act(() => onMsg(wirePing('reply')))
    act(() => onMsg({ type: 'busy', ports: { 0: 'ping', 1: 'apply' } }))
    expect(screen.getByTestId('busy').textContent).toBe('{"0":"ping","1":"apply"}')
    act(() => onMsg(snapshot('p:1')))
    expect(screen.getByTestId(texts(0)).textContent).not.toBe('')
    act(() => onMsg(snapshot('p:2')))
    expect(screen.getByTestId(texts(0)).textContent).toBe('')
    expect(screen.getByTestId('busy').textContent).toBe('{}')
  })

  it('busy replaces the whole map: an empty one means nothing is busy', () => {
    function Busy() { return <span data-testid="busy">{JSON.stringify(useBusy())}</span> }
    mount(<Busy />)
    act(() => onMsg(snapshot('p:1')))
    act(() => onMsg({ type: 'busy', ports: { 0: 'scan' } }))
    expect(screen.getByTestId('busy').textContent).toBe('{"0":"scan"}')
    act(() => onMsg({ type: 'busy', ports: {} }))
    expect(screen.getByTestId('busy').textContent).toBe('{}')
  })
})
