import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { dashboardPrefs } from '../dashboardPrefs'
import { pgidStore } from '../pgidStats'
import { wirePgid, type WirePgid } from '../testPgid'
import { DASH_VIEW_KEY, resetDashViewStore } from '../dashView'
import UPlot from 'uplot'
import { LatencyTab } from './LatencyTable'

const reset = () => { pgidStore.reset(); pgidStore.track('streams', []); pgidStore.track('latency', []) }
beforeEach(() => { localStorage.clear(); resetDashViewStore(); reset() })
afterEach(reset)

const lastMax = (m: WirePgid, id: number, v: number | null) => { m.latency[String(id)].latency.last_max = v; return m }
// The recorded tx sample with the latency stream (pg_id 7) copied to `n` ids in all: 7, 8, 9 ...
function many(n: number): WirePgid {
  const m = wirePgid('tx')
  for (let id = 8; id < 7 + n; id++) {
    m.flow[String(id)] = JSON.parse(JSON.stringify(m.flow['7']))
    m.latency[String(id)] = JSON.parse(JSON.stringify(m.latency['7']))
    m.ver_id[String(id)] = 1
    m.active.latency.push(id)
  }
  return m
}

const table = () => screen.getByRole('table', { name: 'Latency' })
const rowOf = (id: number) => within(table()).getAllByRole('row').find((r) => within(r).queryByRole('rowheader')?.textContent === String(id))!
// The cell under the column header whose text starts with `name`.
const at = (id: number, name: string) => {
  const heads = within(table()).getAllByRole('columnheader').map((h) => h.textContent ?? '')
  const i = heads.findIndex((h) => h.startsWith(name + ' ') || h === name)
  return within(rowOf(id)).getAllByRole('cell')[i - 1]   // the row header is not a cell
}

const HEADS = ['Chart', 'PG ID', 'Max (µs)', 'Server max (µs)', 'Avg (µs)', 'Jitter (µs)', 'Errors']
const heads = () => within(table()).getAllByRole('columnheader').map((h) => h.textContent)

describe('LatencyTab view', () => {
  it('hides Last…Last-9 by default', () => {
    pgidStore.push(wirePgid('tx'))
    render(<LatencyTab />)
    expect(heads()).toEqual(HEADS)
    expect(screen.getByRole('button', { name: 'Show history (Last … Last-9)' })).toHaveAttribute('aria-pressed', 'false')
  })
  it('Show history adds Last…Last-9 after Errors', async () => {
    pgidStore.push(wirePgid('tx'))
    render(<LatencyTab />)
    await userEvent.click(screen.getByRole('button', { name: 'Show history (Last … Last-9)' }))
    const h = heads()
    expect(h).toHaveLength(HEADS.length + 10)
    expect(h.slice(0, HEADS.length)).toEqual(HEADS)
    expect(h[HEADS.length]).toBe('Last (µs)')
    expect(h.at(-1)).toBe('Last-9 (µs)')
    expect(screen.getByRole('columnheader', { name: 'Last (µs)' })).toHaveAttribute('title', "the server's maximum over its latest sample period")
    expect(screen.getByRole('button', { name: 'Show history (Last … Last-9)' })).toHaveAttribute('aria-pressed', 'true')
  })
  it('names the Reset displayed counters button in the Max, Server max and Errors header titles', () => {
    pgidStore.push(wirePgid('tx'))
    render(<LatencyTab />)
    expect(screen.getByRole('columnheader', { name: 'Max (µs)' })).toHaveAttribute('title', 'the largest last max seen since connect or Reset displayed counters')
    expect(screen.getByRole('columnheader', { name: 'Server max (µs)' })).toHaveAttribute('title', "the server's maximum since the stream was added; Reset displayed counters does not reset it")
    expect(screen.getByRole('columnheader', { name: 'Errors' })).toHaveAttribute('title', 'sequence too low plus sequence too high, since Reset displayed counters')
  })
  it('Show history turns off again', async () => {
    pgidStore.push(wirePgid('tx'))
    render(<LatencyTab />)
    const chip = screen.getByRole('button', { name: 'Show history (Last … Last-9)' })
    await userEvent.click(chip)
    await userEvent.click(chip)
    expect(heads()).toEqual(HEADS)
    expect(chip).toHaveAttribute('aria-pressed', 'false')
  })
  it('right-aligns the numbers', () => {
    localStorage.setItem(DASH_VIEW_KEY, '{"history":true}'); resetDashViewStore()
    pgidStore.push(wirePgid('tx'))
    render(<LatencyTab />)
    const tds = Array.from(rowOf(7).children)
    expect(tds).toHaveLength(HEADS.length + 10)
    for (const td of tds.slice(2)) expect(td.tagName === 'TD' && td.classList.contains('num'), td.textContent ?? '').toBe(true)
    expect(tds[0].classList.contains('num')).toBe(false)
    expect(within(rowOf(7)).getByRole('rowheader')).not.toHaveClass('num')
  })
  it('right-aligns the numeric headers', () => {
    localStorage.setItem(DASH_VIEW_KEY, '{"history":true}'); resetDashViewStore()
    pgidStore.push(wirePgid('tx'))
    render(<LatencyTab />)
    const ths = within(table()).getAllByRole('columnheader')
    expect(ths).toHaveLength(HEADS.length + 10)
    for (const th of ths.slice(2)) expect(th, th.textContent ?? '').toHaveClass('num')
    for (const th of ths.slice(0, 2)) expect(th, th.textContent ?? '').not.toHaveClass('num')
  })
  it('Full-width table stacks the chart', async () => {
    pgidStore.push(wirePgid('tx'))
    const { container } = render(<LatencyTab />)
    expect(container.querySelector('.overview-grid')).not.toHaveClass('wide')
    const chip = screen.getByRole('button', { name: 'Full-width table' })
    expect(chip).toHaveAttribute('aria-pressed', 'false')
    await userEvent.click(chip)
    expect(chip).toHaveAttribute('aria-pressed', 'true')
    expect(container.querySelector('.overview-grid')).toHaveClass('wide')
  })
})

describe('LatencyTab table', () => {
  beforeEach(() => { localStorage.setItem(DASH_VIEW_KEY, '{"history":true}'); resetDashViewStore() })
  it('renders the recorded tx sample: pg_id 7 with its numbers', () => {
    pgidStore.push(wirePgid('tx'))
    render(<LatencyTab />)
    expect(within(table()).getAllByRole('row')).toHaveLength(2)
    expect(at(7, 'Max')).toHaveTextContent('1,135')
    expect(at(7, 'Server max')).toHaveTextContent('1,174')
    expect(at(7, 'Avg')).toHaveTextContent('500')
    expect(at(7, 'Last')).toHaveTextContent('1,135')
    expect(at(7, 'Last-1')).toHaveTextContent('–')
    expect(at(7, 'Jitter')).toHaveTextContent('223')
    expect(at(7, 'Errors')).toHaveTextContent('0')
  })
  it('has Last through Last-9 and says what Last is', () => {
    pgidStore.push(wirePgid('tx'))
    render(<LatencyTab />)
    expect(screen.getByRole('columnheader', { name: 'Last-9 (µs)' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Last (µs)' })).toHaveAttribute('title', "the server's maximum over its latest sample period")
  })
  it('shifts the window by one per message', () => {
    for (const v of [100, 200, 300]) act(() => pgidStore.push(lastMax(wirePgid('tx'), 7, v)))
    render(<LatencyTab />)
    expect(at(7, 'Last')).toHaveTextContent('300')
    expect(at(7, 'Last-1')).toHaveTextContent('200')
    expect(at(7, 'Last-2')).toHaveTextContent('100')
    expect(at(7, 'Last-3')).toHaveTextContent('–')
    act(() => pgidStore.push(lastMax(wirePgid('tx'), 7, 400)))
    expect(at(7, 'Last')).toHaveTextContent('400')
    expect(at(7, 'Last-1')).toHaveTextContent('300')
    expect(at(7, 'Last-3')).toHaveTextContent('100')
  })
  it('Max does not fall when Last falls, and Clear resets it; Server max survives Clear', () => {
    pgidStore.push(lastMax(wirePgid('tx'), 7, 900))
    render(<LatencyTab />)
    act(() => pgidStore.push(lastMax(wirePgid('tx'), 7, 50)))
    expect(at(7, 'Last')).toHaveTextContent('50')
    expect(at(7, 'Max')).toHaveTextContent('900')
    act(() => pgidStore.clear())
    expect(at(7, 'Max')).toHaveTextContent('–')
    expect(at(7, 'Last')).toHaveTextContent('–')
    expect(at(7, 'Server max')).toHaveTextContent('1,174')
  })
  it('Errors is seq_too_low + seq_too_high only', () => {
    const m = wirePgid('tx')
    Object.assign(m.latency['7'].err_cntrs, { dropped: 100, dup: 200, out_of_order: 300, seq_too_high: 4, seq_too_low: 5 })
    pgidStore.push(m)
    render(<LatencyTab />)
    expect(at(7, 'Errors')).toHaveTextContent(/^9$/)
  })
  it('shows – for a None average and Errors when the server sent no counters', () => {
    const m = wirePgid('tx')
    m.latency['7'].latency.average = null
    m.latency['7'].err_cntrs = {}
    pgidStore.push(m)
    render(<LatencyTab />)
    expect(at(7, 'Avg')).toHaveTextContent(/^–$/)
    expect(at(7, 'Errors')).toHaveTextContent(/^–$/)
  })
  it('keeps the Last columns of a latency pg_id that is not charted', async () => {
    pgidStore.push(many(6))   // ids 7..12; the first four are charted on sight
    render(<LatencyTab />)
    expect(screen.getByRole('checkbox', { name: 'Chart PG ID 12' })).not.toBeChecked()
    expect(at(12, 'Last')).toHaveTextContent('1,135')
    act(() => pgidStore.push(lastMax(many(6), 12, 70)))
    expect(at(12, 'Last')).toHaveTextContent('70')
    expect(at(12, 'Last-1')).toHaveTextContent('1,135')
    expect(at(12, 'Max')).toHaveTextContent('1,135')
    await userEvent.click(screen.getByRole('checkbox', { name: 'Chart PG ID 12' }))
    expect(screen.getByRole('checkbox', { name: 'Chart PG ID 12' })).toBeChecked()
  })
  it('renders no table or histogram while hidden, but still tracks the chart selection', () => {
    pgidStore.push(wirePgid('tx'))
    render(<LatencyTab visible={false} />)
    expect(screen.queryByRole('table')).toBeNull()
    expect(screen.queryByRole('region', { name: 'Latency histogram' })).toBeNull()
    expect(screen.getByRole('figure', { name: /Latency/ })).toBeInTheDocument()
    act(() => pgidStore.push(wirePgid('tx')))
    expect(pgidStore.series(7, 'lat_avg', 60)).toHaveLength(1)   // history starts when the pg_id is selected: the push after mount
  })
  it('offers the four chart metrics', () => {
    pgidStore.push(wirePgid('tx'))
    render(<LatencyTab />)
    const opts = within(screen.getByRole('combobox', { name: 'Latency chart metric' })).getAllByRole('option').map((o) => o.textContent)
    expect(opts).toEqual(['Average', 'Max', 'Last max', 'Jitter'])
  })
  it('redraws at once when the metric changes, on the same uPlot, and the select keeps focus', async () => {
    pgidStore.push(wirePgid('tx'))
    render(<LatencyTab />)
    act(() => pgidStore.push(wirePgid('tx')))   // history starts when a pg_id is selected
    const mock = UPlot as unknown as { instances: { setDataCalls: (number | null)[][][]; data: (number | null)[][] }[] }
    const count = mock.instances.length
    const u = mock.instances.at(-1)!
    expect(u.data[1]).toEqual([500])   // average
    const select = screen.getByRole('combobox', { name: 'Latency chart metric' })
    const calls = u.setDataCalls.length
    select.focus()
    await userEvent.selectOptions(select, 'Jitter')
    expect(mock.instances).toHaveLength(count)
    expect(u.setDataCalls.length).toBeGreaterThan(calls)
    expect(u.setDataCalls.at(-1)![1]).toEqual([223])
    expect(document.activeElement).toBe(screen.getByRole('combobox', { name: 'Latency chart metric' }))
  })
  it('falls back when the stored histogram id is gone', () => {
    pgidStore.push(many(6))   // ids 7..12
    dashboardPrefs.set({ histId: 99 })
    render(<LatencyTab />)
    expect(screen.getByRole('combobox', { name: 'Histogram PG ID' })).toHaveValue('7')
  })
  it('keeps the histogram pg_id across a tab switch (the tab stays mounted)', async () => {
    pgidStore.push(many(2))
    const { rerender } = render(<LatencyTab visible />)
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Histogram PG ID' }), '8')
    rerender(<LatencyTab visible={false} />)
    rerender(<LatencyTab visible />)
    expect(screen.getByRole('combobox', { name: 'Histogram PG ID' })).toHaveValue('8')
  })
  it('describes a blocked checkbox by the per-tab cap reason', async () => {
    pgidStore.push(many(10))   // ids 7..16; 7..10 are charted on sight
    render(<LatencyTab />)
    for (const id of [11, 12, 13, 14]) await userEvent.click(screen.getByRole('checkbox', { name: `Chart PG ID ${id}` }))
    expect(screen.getByRole('checkbox', { name: 'Chart PG ID 15' })).toHaveAttribute('aria-describedby', 'pgid-cap-reason-latency')
    expect(document.getElementById('pgid-cap-reason-latency')).toHaveTextContent(/At most 8/)
  })
  it('says so when no latency stream is active', () => {
    const m = wirePgid('tx')
    m.active.latency = []
    pgidStore.push(m)
    render(<LatencyTab />)
    expect(screen.getByText('No streams with latency are active.')).toBeInTheDocument()
  })
  it('shows no table controls with the empty-state message', () => {
    const m = wirePgid('tx')
    m.active.latency = []
    pgidStore.push(m)
    render(<LatencyTab />)
    expect(screen.getByText('No streams with latency are active.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Show history (Last … Last-9)' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Full-width table' })).toBeNull()
  })
})
