import { act, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import UPlot from 'uplot'
import { wireUtil, type WireUtil } from '../testPgid'
import { utilStore } from '../utilStats'
import { roundHalfEven } from '../utilTables'
import { CpuChart, CpuTable } from './CpuTable'

beforeEach(() => utilStore.reset())
afterEach(() => utilStore.reset())

// Cores of the recorded tx sample replaced by `cores`; the other fields are as recorded.
const withCores = (cores: { ports: number[]; history: (number | null)[] }[]): WireUtil => ({ ...wireUtil('tx'), cpu: cores })
const rows = () => within(screen.getByRole('table', { name: 'CPU utilization' })).getAllByRole('row').slice(1)
const cells = (i: number) => within(rows()[i]).getAllByRole('cell').map((c) => c.textContent)
const plots = () => (UPlot as unknown as { instances: { opts: { series: { label?: string; stroke?: string }[] }; data: unknown[][] }[] }).instances

// Expected values: `to_table_cpu` of the vendored client, fed the get_utilization result recorded at t=6.235 in
// tests/fixtures/p3a/rpc.jsonl (the sample utilMsg.json's tx was taken from). It prints
//   " 0   (0)   |   2 |      4 |   3 |   1 |   0 ..."   (Thread, Avg, Latest, then history[1:])
describe('CpuTable', () => {
  it('shows the recorded tx sample as to_table_cpu prints it: core 0 on port 0, Avg 2, Latest 4', () => {
    utilStore.push(wireUtil('tx'))
    render(<CpuTable />)
    expect(rows()).toHaveLength(1)
    expect(within(rows()[0]).getByRole('rowheader')).toHaveTextContent('Core 0')
    expect(cells(0)).toEqual(['0', '2', '4'])
  })
  it('shows "idle" for a core with both ports -1, and the single port of a half-used core', () => {
    // vendored to_table_cpu on these three cores (history of 4): " 0  (0,1)  | 2 | 2", " 1 (IDLE)  | 4 | 3", " 2   (1)   | 0 | 0"
    utilStore.push(withCores([
      { ports: [0, 1], history: [2, 3, 2, 3] }, { ports: [-1, -1], history: [3, 4, 3, 4] }, { ports: [-1, 1], history: [0, 1, 0, 0] },
    ]))
    render(<CpuTable />)
    expect(cells(0)).toEqual(['0, 1', '2', '2'])
    expect(cells(1)).toEqual(['idle', '4', '3'])
    expect(cells(2)).toEqual(['1', '0', '0'])
  })
  it('rounds the Avg half to even, as Python round() does', () => {
    // sum(history[:min(5, len)]) / min(5, len) with a history of 2: python int(round(x)) is 0 for 0.5, 2 for 1.5, 2 for 2.5, 4 for 3.5
    utilStore.push(withCores([0, 2, 4, 6].map((n) => ({ ports: [0, -1], history: [n, 1] }))))
    render(<CpuTable />)
    expect([0, 1, 2, 3].map((i) => cells(i)[1])).toEqual(['0', '2', '2', '4'])
    expect([0.5, 1.5, 2.5, 3.5, 4.5].map(roundHalfEven)).toEqual([0, 2, 2, 4, 4])
  })
  it('averages only the newest 5 of a 20-sample history', () => {
    utilStore.push(withCores([{ ports: [0, -1], history: [10, 0, 0, 0, 0, 100, 100, 100, 100, 100, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0] }]))
    render(<CpuTable />)
    expect(cells(0)).toEqual(['0', '2', '10'])
  })
  it('shows a dash, never NaN, for a null sample or an empty history', () => {
    utilStore.push(withCores([{ ports: [0, -1], history: [null, 1, 2, 3, 4] }, { ports: [1, -1], history: [] }]))
    render(<CpuTable />)
    expect(cells(0)).toEqual(['0', '–', '–'])
    expect(cells(1)).toEqual(['1', '–', '–'])
  })
  it('says so when there is no sample yet', () => {
    render(<CpuTable />)
    expect(screen.queryByRole('table')).toBeNull()
    expect(screen.getByText('No utilization data yet.')).toBeInTheDocument()
  })
  it('follows the store', () => {
    utilStore.push(wireUtil('tx'))
    render(<CpuTable />)
    act(() => utilStore.push({ ...withCores([{ ports: [0, -1], history: [7, 7, 7, 7, 7] }]), ts: 1 }))
    expect(cells(0)).toEqual(['0', '7', '7'])
  })
})

describe('CpuChart', () => {
  it('has a series per core, named Core N (ports a, b), with an idle core as Core N (idle)', () => {
    utilStore.push(withCores([
      { ports: [0, 1], history: [2, 3] }, { ports: [-1, -1], history: [3, 4] }, { ports: [-1, 1], history: [0, 1] },
    ]))
    render(<CpuChart />)
    const s = plots().at(-1)!.opts.series.slice(1)
    expect(s.map((x) => x.label)).toEqual(['Core 0 (ports 0, 1)', 'Core 1 (idle)', 'Core 2 (port 1)'])
    expect(new Set(s.map((x) => x.stroke)).size).toBe(3)
    expect(screen.queryByText(/first 8/)).toBeNull()
  })
  it('charts the history of each core, oldest first, one point per second back from ts', () => {
    utilStore.push(wireUtil('tx'))   // ts 0, 20 samples, newest first: 4, 3, 1, 0 ... 0, 1, 2, 0
    render(<CpuChart />)
    const d = plots().at(-1)!.data as number[][]
    expect(d[0]).toHaveLength(20)
    expect(d[0].at(-1)).toBe(0)
    expect(d[1].slice(-3)).toEqual([1, 3, 4])
  })
  it('charts the first 8 of 10 cores and says so', () => {
    utilStore.push(withCores(Array.from({ length: 10 }, (_, i) => ({ ports: [i, -1], history: [i, i] }))))
    render(<CpuChart />)
    expect(plots().at(-1)!.opts.series.slice(1)).toHaveLength(8)
    expect(plots().at(-1)!.opts.series[8].label).toBe('Core 7 (port 7)')
    expect(screen.getByText('Charting the first 8 of 10 cores.')).toBeInTheDocument()
  })
  it('keeps its uPlot across store updates (history is not rebuilt)', () => {
    utilStore.push(wireUtil('tx'))
    render(<CpuChart />)
    const n = plots().length
    act(() => utilStore.push({ ...wireUtil('tx'), ts: 1 }))
    expect(plots()).toHaveLength(n)
  })
})
