import { act, cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import * as state from '../state'
import { dashboardPrefs } from '../dashboardPrefs'
import { pgidStore } from '../pgidStats'
import { statsStore } from '../stats'
import { wirePgid, wireUtil } from '../testPgid'
import { utilStore } from '../utilStats'
import { statsFromSample } from '../testStats'
import { DashboardPage } from './DashboardPage'

// A stand-in that shows the props the Dashboard hands each chart.
vi.mock('../components/TimeChart', () => ({
  TimeChart: (p: { title: string; yFrom0?: boolean; controls?: ReactNode }) => (
    <figure aria-label={p.title} data-yfrom0={String(p.yFrom0 ?? false)}>{p.controls}</figure>
  ),
}))
afterEach(() => { dashboardPrefs.reset(); vi.useRealTimers(); vi.restoreAllMocks(); pgidStore.reset() })

const session = (st: api.SessionSnapshot['state']): api.SessionSnapshot => ({ type: 'session', state: st, params: null, info: null, error: null, rpc_console: false, include_polling: false })

describe('DashboardPage Clear stats', () => {
  it('calls statsStore.clear and nothing else when connected', async () => {
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, session: session('connected') })
    const clear = vi.spyOn(statsStore, 'clear')
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    const stop = vi.spyOn(api, 'stop')
    render(<DashboardPage />)
    await userEvent.click(screen.getByRole('button', { name: 'Clear stats' }))
    expect(clear).toHaveBeenCalledTimes(1)
    expect(fetchSpy).not.toHaveBeenCalled()
    expect(stop).not.toHaveBeenCalled()
  })
  it('also clears the PGID counters: after Clear, pg_id 5 Tx pkts reads 0', async () => {
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, session: session('connected') })
    pgidStore.reset()
    pgidStore.push(wirePgid('tx'))
    expect(pgidStore.relative(5, 'tx_pkts', 'total')).toBe(90909)
    render(<DashboardPage />)
    await userEvent.click(screen.getByRole('button', { name: 'Clear stats' }))
    expect(pgidStore.relative(5, 'tx_pkts', 'total')).toBe(0)
  })
  it('Clear stats zeroes the rendered Streams table counters', async () => {
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, session: session('connected') })
    pgidStore.reset()
    pgidStore.push(wirePgid('tx'))
    render(<DashboardPage />)
    await userEvent.click(screen.getByRole('tab', { name: 'Streams' }))
    const row = () => within(screen.getByRole('table', { name: 'Streams' })).getAllByRole('row')[2]
    expect(within(row()).getAllByText('90,909')).toHaveLength(2)
    await userEvent.click(screen.getByRole('button', { name: 'Clear stats' }))
    expect(within(row()).queryByText('90,909')).toBeNull()
  })
  it('Clear stats empties the Latency tab Max, and renders its table only while that tab is shown', async () => {
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, session: session('connected') })
    pgidStore.reset()
    pgidStore.push(wirePgid('tx'))
    render(<DashboardPage />)
    expect(screen.queryByRole('table', { name: 'Latency' })).toBeNull()
    await userEvent.click(screen.getByRole('tab', { name: 'Latency' }))
    const row = () => within(screen.getByRole('table', { name: 'Latency' })).getAllByRole('row')[1]
    expect(within(row()).getAllByText('1,135').length).toBeGreaterThanOrEqual(2)   // Max and Last
    await userEvent.click(screen.getByRole('button', { name: 'Clear stats' }))
    expect(within(row()).queryByText('1,135')).toBeNull()
    await userEvent.click(screen.getByRole('tab', { name: 'Overview' }))
    expect(screen.queryByRole('table', { name: 'Latency' })).toBeNull()
  })
  it('Clear stats sits at the end of the tab strip', () => {
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, session: session('connected') })
    const { container } = render(<DashboardPage />)
    const end = container.querySelector('.tabs-end')!
    expect(within(end as HTMLElement).getByRole('button', { name: 'Clear stats' })).toBeInTheDocument()
    expect(within(screen.getByRole('tablist', { name: 'Dashboard' })).queryByRole('button', { name: 'Clear stats' })).toBeNull()
  })
  it('is disabled unless the session is connected', () => {
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, session: session('lost') })
    render(<DashboardPage />)
    expect(screen.getByRole('button', { name: 'Clear stats' })).toBeDisabled()
  })
})

describe('DashboardPage stale dimming', () => {
  it('gets is-stale after 3 s without a push and loses it on a push', () => {
    vi.useFakeTimers()
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, session: session('connected') })
    statsStore.push(statsFromSample(3, { interval_s: 1 }))
    render(<DashboardPage />)
    const root = screen.getByRole('tabpanel', { name: 'Overview' })
    expect(root).not.toHaveClass('is-stale')
    act(() => { vi.advanceTimersByTime(4000) })
    expect(root).toHaveClass('is-stale')
    // One notice for the tab: the banner, and no second marker among the tiles.
    expect(within(root).getAllByRole('status')).toHaveLength(1)
    expect(within(root).getByRole('status', { name: 'Statistics are stale' })).toBeInTheDocument()
    act(() => statsStore.push(statsFromSample(3, { interval_s: 1 })))
    expect(root).not.toHaveClass('is-stale')
  })
})

describe('DashboardPage tabs', () => {
  it('the service-mode banner sits above the tabs and stays on every tab (RC1-21)', async () => {
    const svc = { id: 0, state: 'IDLE', owner: '', owned: false, service: true, service_filtered: false, profiles: {} } as unknown as api.PortView
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, session: session('connected'), ports: [svc] })
    render(<DashboardPage />)
    const banner = screen.getByRole('status', { name: 'Service mode' })
    expect(banner.compareDocumentPosition(screen.getByRole('tablist')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    await userEvent.click(screen.getByRole('tab', { name: 'Latency' }))
    expect(screen.getByRole('status', { name: 'Service mode' })).toBeVisible()
  })
  it('a port in filtered service mode is named in the page banner too (review T5)', () => {
    const svc = { id: 1, state: 'IDLE', owner: '', owned: false, service: false, service_filtered: true, profiles: {} } as unknown as api.PortView
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, session: session('connected'), ports: [svc] })
    render(<DashboardPage />)
    expect(screen.getByRole('status', { name: 'Service mode' })).toHaveTextContent(/^Service mode is on for port 1\./)
  })
  it('connection lost uses the same bright banner', () => {
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, session: session('lost') })
    render(<DashboardPage />)
    expect(screen.getByRole('status', { name: 'Connection lost' })).toHaveClass('banner', 'alert')
    expect(screen.getByRole('status', { name: 'Connection lost' }).querySelector('strong')).toHaveTextContent('Connection lost:')
  })
  it('has the four tabs; Overview is selected and the arrow keys, Home and End move between them', async () => {
    render(<DashboardPage />)
    const names = screen.getAllByRole('tab').map((t) => t.textContent)
    expect(names).toEqual(['Overview', 'Streams', 'Latency', 'Utilization'])
    expect(screen.getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'true')
    screen.getByRole('tab', { name: 'Overview' }).focus()
    await userEvent.keyboard('{ArrowRight}')
    expect(screen.getByRole('tab', { name: 'Streams' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'Streams' })).toHaveFocus()
    expect(screen.getByRole('tabpanel', { name: 'Streams' })).toBeVisible()
    expect(screen.queryByRole('tabpanel', { name: 'Overview' })).toBeNull()   // hidden
    await userEvent.keyboard('{End}')
    expect(screen.getByRole('tab', { name: 'Utilization' })).toHaveFocus()
    await userEvent.keyboard('{ArrowRight}')
    expect(screen.getByRole('tab', { name: 'Overview' })).toHaveFocus()
    await userEvent.keyboard('{ArrowLeft}')
    expect(screen.getByRole('tab', { name: 'Utilization' })).toHaveAttribute('aria-selected', 'true')
    await userEvent.keyboard('{Home}')
    expect(screen.getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'true')
  })
  it('stats arriving while pgid stops makes the Streams tab stale and not the Overview', async () => {
    vi.useFakeTimers()
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, session: session('connected') })
    pgidStore.push(wirePgid('tx'))
    statsStore.push(statsFromSample(3))
    render(<DashboardPage />)
    for (let i = 0; i < 5; i++) {
      act(() => { vi.advanceTimersByTime(1000) })
      act(() => statsStore.push(statsFromSample(3)))
    }
    expect(screen.getByRole('tabpanel', { name: 'Overview' })).not.toHaveClass('is-stale')
    act(() => { screen.getByRole('tab', { name: 'Streams' }).click() })
    const panel = screen.getByRole('tabpanel', { name: 'Streams' })
    expect(panel).toHaveClass('is-stale')
    expect(within(panel).getByRole('status')).toHaveTextContent(/^Stream stats are not updating \(last update \d\d:\d\d:\d\d\)$/)
    act(() => { screen.getByRole('tab', { name: 'Latency' }).click() })
    expect(screen.getByRole('tabpanel', { name: 'Latency' })).toHaveClass('is-stale')
    act(() => { pgidStore.push(wirePgid('tx')) })
    expect(screen.getByRole('tabpanel', { name: 'Latency' })).not.toHaveClass('is-stale')
  })
  it('the Utilization tab is judged by the util read alone', () => {
    vi.useFakeTimers()
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, session: session('connected') })
    utilStore.reset()
    utilStore.push(wireUtil('tx') as never)
    statsStore.push(statsFromSample(3))
    render(<DashboardPage />)
    act(() => { screen.getByRole('tab', { name: 'Utilization' }).click() })
    for (let i = 0; i < 5; i++) {
      act(() => { vi.advanceTimersByTime(1000) })
      act(() => statsStore.push(statsFromSample(3)))
    }
    expect(screen.getByRole('tabpanel', { name: 'Utilization' })).toHaveClass('is-stale')
    expect(screen.getByRole('status', { name: 'Utilization stats are stale' })).toBeInTheDocument()
    act(() => utilStore.push(wireUtil('tx') as never))
    expect(screen.getByRole('tabpanel', { name: 'Utilization' })).not.toHaveClass('is-stale')
    utilStore.reset()
  })
})

describe('DashboardPage Utilization tab', () => {
  it('shows the CPU and mbuf tables only while the tab is shown, and keeps the chart mounted', () => {
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, session: session('connected') })
    utilStore.reset()
    utilStore.push(wireUtil('tx') as never)
    render(<DashboardPage />)
    expect(screen.queryByRole('table', { name: 'CPU utilization' })).toBeNull()
    expect(screen.queryByRole('table', { name: 'Mbuf utilization' })).toBeNull()
    expect(screen.getByRole('figure', { name: 'CPU utilization over time', hidden: true })).toBeInTheDocument()
    act(() => { screen.getByRole('tab', { name: 'Utilization' }).click() })
    expect(screen.getByRole('table', { name: 'CPU utilization' })).toBeInTheDocument()
    expect(screen.getByRole('table', { name: 'Mbuf utilization' })).toBeInTheDocument()
    utilStore.reset()
  })
})

describe('DashboardPage lost banner', () => {
  it.each(['connected', 'connecting', 'disconnected'] as const)('is absent while %s', (st) => {
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, session: session(st) })
    render(<DashboardPage />)
    expect(screen.queryByText(/Connection lost/)).toBeNull()
  })
  it('shows while the session is lost', () => {
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, session: session('lost') })
    render(<DashboardPage />)
    expect(screen.getByRole('status', { name: 'Connection lost' })).toHaveTextContent('Connection lost: values are the last received')
  })
})

// ids 1..n with flow and latency entries each, ts as given.
function manyPgids(n: number, ts: number) {
  const m = wirePgid('tx')
  const ids = Array.from({ length: n }, (_, i) => i + 1)
  const flow = m.flow['5']
  const lat = m.latency['7']
  m.flow = Object.fromEntries(ids.map((i) => [String(i), JSON.parse(JSON.stringify(flow))]))
  m.latency = Object.fromEntries(ids.map((i) => [String(i), JSON.parse(JSON.stringify(lat))]))
  m.ver_id = Object.fromEntries(ids.map((i) => [String(i), 0]))
  m.active = { flow_stats: ids, latency: ids }
  m.ts = ts
  return m
}

describe('DashboardPage keeps the chart selection while the page is away', () => {
  const ticked = () => screen.getAllByRole('checkbox', { name: /^Chart PG ID/ }).filter((c) => (c as HTMLInputElement).checked).map((c) => c.getAttribute('aria-label'))
  afterEach(() => { cleanup(); pgidStore.reset() })

  it('Dashboard to another page and back: same Latency selection, same chart history', async () => {
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, session: session('connected') })
    pgidStore.reset()
    pgidStore.push(manyPgids(6, 100))
    const first = render(<DashboardPage />)
    await userEvent.click(screen.getByRole('tab', { name: 'Latency' }))
    expect(ticked()).toEqual(['Chart PG ID 1', 'Chart PG ID 2', 'Chart PG ID 3', 'Chart PG ID 4'])
    await userEvent.click(screen.getByRole('checkbox', { name: 'Chart PG ID 1' }))
    await userEvent.click(screen.getByRole('checkbox', { name: 'Chart PG ID 6' }))
    const before = ticked()
    expect(before).toEqual(['Chart PG ID 2', 'Chart PG ID 3', 'Chart PG ID 4', 'Chart PG ID 6'])
    act(() => pgidStore.push(manyPgids(6, 101)))
    act(() => pgidStore.push(manyPgids(6, 102)))
    expect(pgidStore.series(6, 'lat_avg', 60)).toHaveLength(2)   // 6 was picked after the first push
    expect(pgidStore.series(2, 'lat_avg', 60)).toHaveLength(2)   // the push before the first render was not tracked
    const colours = pgidStore.selection('latency').picked.map((p) => p.color)

    first.unmount()   // navigating to the Control page unmounts the Dashboard route
    expect(pgidStore.series(2, 'lat_avg', 60)).toHaveLength(2)

    render(<DashboardPage />)
    await userEvent.click(screen.getByRole('tab', { name: 'Latency' }))
    expect(ticked()).toEqual(before)
    expect(pgidStore.selection('latency').picked.map((p) => p.color)).toEqual(colours)
    expect(pgidStore.series(2, 'lat_avg', 60)).toHaveLength(2)
    act(() => pgidStore.push(manyPgids(6, 103)))
    expect(pgidStore.series(2, 'lat_avg', 60)).toHaveLength(3)
  })
  it('a connect_seq change (store reset) still clears the selection and the next ids seed afresh', async () => {
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, session: session('connected') })
    pgidStore.reset()
    pgidStore.push(manyPgids(6, 100))
    render(<DashboardPage />)
    await userEvent.click(screen.getByRole('tab', { name: 'Latency' }))
    await userEvent.click(screen.getByRole('checkbox', { name: 'Chart PG ID 1' }))
    expect(ticked()).toHaveLength(3)
    act(() => pgidStore.reset())
    expect(pgidStore.selection('latency').picked).toEqual([])
    act(() => pgidStore.push(manyPgids(6, 200)))
    expect(ticked()).toEqual(['Chart PG ID 1', 'Chart PG ID 2', 'Chart PG ID 3', 'Chart PG ID 4'])
  })
})

describe('DashboardPage stale before the first message', () => {
  it('Streams is stale once connected for 3 x interval with no pgid message, and does not claim there are no streams', () => {
    vi.useFakeTimers()
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, session: session('connected') })
    pgidStore.reset()
    render(<DashboardPage />)
    act(() => { screen.getByRole('tab', { name: 'Streams' }).click() })
    const panel = screen.getByRole('tabpanel', { name: 'Streams' })
    act(() => { vi.advanceTimersByTime(3000) })
    expect(panel).not.toHaveClass('is-stale')
    expect(within(panel).getByText(/^No streams with a PG ID are active\. A stream gets one in Studio > Properties/)).toBeInTheDocument()
    act(() => { vi.advanceTimersByTime(1000) })
    expect(panel).toHaveClass('is-stale')
    expect(within(panel).getByRole('status', { name: 'Stream stats are stale' })).toBeInTheDocument()
    expect(within(panel).queryByText(/No streams/)).toBeNull()
    act(() => { pgidStore.push(wirePgid('tx')) })
    expect(panel).not.toHaveClass('is-stale')
  })
  it('Latency does not claim there are no streams either', () => {
    vi.useFakeTimers()
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, session: session('connected') })
    pgidStore.reset()
    render(<DashboardPage />)
    act(() => { screen.getByRole('tab', { name: 'Latency' }).click() })
    act(() => { vi.advanceTimersByTime(4000) })
    const panel = screen.getByRole('tabpanel', { name: 'Latency' })
    expect(panel).toHaveClass('is-stale')
    expect(within(panel).queryByText(/No streams/)).toBeNull()
  })
  it('Utilization is stale when no util message ever arrives', () => {
    vi.useFakeTimers()
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, session: session('connected') })
    utilStore.reset()
    render(<DashboardPage />)
    act(() => { screen.getByRole('tab', { name: 'Utilization' }).click() })
    const panel = screen.getByRole('tabpanel', { name: 'Utilization' })
    act(() => { vi.advanceTimersByTime(3000) })
    expect(panel).not.toHaveClass('is-stale')
    act(() => { vi.advanceTimersByTime(1000) })
    expect(panel).toHaveClass('is-stale')
    act(() => utilStore.push(wireUtil('tx') as never))
    expect(panel).not.toHaveClass('is-stale')
    utilStore.reset()
  })
  it('nothing is stale while the session is not connected', () => {
    vi.useFakeTimers()
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, session: session('connecting') })
    pgidStore.reset()
    statsStore.onSession('connected'); statsStore.onSession('disconnected')   // no stats message left over from an earlier test
    render(<DashboardPage />)
    act(() => { vi.advanceTimersByTime(10000) })
    expect(screen.getByRole('tabpanel', { name: 'Overview' })).not.toHaveClass('is-stale')
  })
})

describe('DashboardPage preferences', () => {
  it('the tab and interval survive unmount and remount', async () => {
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, session: session('connected') })
    const first = render(<DashboardPage />)
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Interval' }), '300')
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Layout' }), '4')
    await userEvent.click(screen.getByRole('tab', { name: 'Latency' }))
    first.unmount()
    render(<DashboardPage />)
    expect(screen.getByRole('tab', { name: 'Latency' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('combobox', { name: 'Interval', hidden: true })).toHaveValue('300')
    expect(screen.getByRole('combobox', { name: 'Layout', hidden: true })).toHaveValue('4')
  })
})

describe('DashboardPage Y axis', () => {
  it('marks the pressed button with the chip class that index.css styles for aria-pressed', () => {
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, session: session('connected') })
    render(<DashboardPage />)
    const g = screen.getAllByRole('group', { name: 'Y axis', hidden: true })[0]
    expect(g).toHaveClass('y-axis-toggle')
    for (const name of ['Fit', 'From 0']) expect(within(g).getByRole('button', { name, hidden: true })).toHaveClass('chip')
  })
  it('is Fit by default; From 0 on Overview reaches the Streams, Latency and Utilization tabs and every chart', async () => {
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, session: session('connected') })
    pgidStore.reset()
    pgidStore.push(wirePgid('tx'))
    utilStore.reset()
    utilStore.push(wireUtil('tx') as never)
    render(<DashboardPage />)
    const fromOf = (name: RegExp | string) => screen.getAllByRole('figure', { name, hidden: true }).map((f) => f.getAttribute('data-yfrom0'))
    const groups = () => screen.getAllByRole('group', { name: 'Y axis', hidden: true })
    expect(groups()).toHaveLength(4)   // Overview, Streams, Latency, Utilization
    groups().forEach((g) => expect(within(g).getByRole('button', { name: 'Fit', hidden: true })).toHaveAttribute('aria-pressed', 'true'))
    expect(fromOf(/^(Streams|Latency|CPU)/)).toEqual(['false', 'false', 'false'])
    await userEvent.click(within(groups()[0]).getByRole('button', { name: 'From 0' }))
    groups().forEach((g) => {
      expect(within(g).getByRole('button', { name: 'From 0', hidden: true })).toHaveAttribute('aria-pressed', 'true')
      expect(within(g).getByRole('button', { name: 'Fit', hidden: true })).toHaveAttribute('aria-pressed', 'false')
    })
    expect(fromOf(/^(Streams|Latency|CPU)/)).toEqual(['true', 'true', 'true'])
    act(() => dashboardPrefs.reset())
    groups().forEach((g) => expect(within(g).getByRole('button', { name: 'Fit', hidden: true })).toHaveAttribute('aria-pressed', 'true'))
    expect(fromOf(/^(Streams|Latency|CPU)/)).toEqual(['false', 'false', 'false'])
  })
})
