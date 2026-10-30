import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { pgidStore } from '../pgidStats'
import { wirePgid, type WirePgid } from '../testPgid'
import UPlot from 'uplot'
import { LatencyTab } from './LatencyTable'

const reset = () => { pgidStore.reset(); pgidStore.track('streams', []); pgidStore.track('latency', []) }
beforeEach(reset)
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

describe('LatencyTab table', () => {
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
})
