import { useState } from 'react'
import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { pgidStore } from '../pgidStats'
import { wirePgid, type WirePgid } from '../testPgid'
import { bucketRange, topBuckets } from '../latencyBuckets'
import { LatencyHistogram as Real } from './LatencyHistogram'

// The caller owns the chosen pg_id, as LatencyTab does.
function LatencyHistogram({ ids }: { ids: number[] }) {
  const [c, setC] = useState<number | null>(null)
  return <Real ids={ids} chosen={c} onChoose={setC} />
}
const reset = () => { localStorage.clear(); pgidStore.reset(); pgidStore.track('streams', []); pgidStore.track('latency', []) }
beforeEach(reset)
afterEach(reset)

const bars = () => screen.queryAllByTestId('hist-bar')
const withHist = (h: Record<string, number>): WirePgid => { const m = wirePgid('tx'); m.latency['7'].latency.histogram = h; return m }

describe('LatencyHistogram', () => {
  it('shows at most 11 bars for the recorded sample (19 buckets), the largest keys, ascending, each with text', () => {
    pgidStore.push(wirePgid('tx'))
    render(<LatencyHistogram ids={[7]} />)
    const b = bars()
    expect(b).toHaveLength(11)
    // keys 1000, 900, 800, 700, 600, 500, 400, 300, 200, 100, 90 are the 11 largest
    expect(b[0]).toHaveTextContent('90–100 µs')
    expect(b[0]).toHaveTextContent('4')
    expect(b[1]).toHaveTextContent('100–200 µs')
    expect(b[1]).toHaveTextContent('74')
    expect(b[10]).toHaveTextContent('1,000–2,000 µs')
    expect(b[10]).toHaveTextContent('46')
  })
  it('keeps the largest keys, not the largest counts', () => {
    const h: Record<string, number> = {}
    for (let k = 10; k <= 90; k += 10) h[String(k)] = 1000
    h['2000'] = 1; h['3000'] = 1; h['100'] = 1000
    pgidStore.push(withHist(h))
    render(<LatencyHistogram ids={[7]} />)
    expect(bars()).toHaveLength(11)
    expect(bars()[10]).toHaveTextContent('3,000–4,000 µs')
    expect(bars()[9]).toHaveTextContent('2,000–3,000 µs')
    expect(bars()[0]).toHaveTextContent('20–30 µs')
  })
  it('ignores zero-count buckets', () => {
    pgidStore.push(withHist({ '100': 0, '200': 3 }))
    render(<LatencyHistogram ids={[7]} />)
    expect(bars()).toHaveLength(1)
  })
  it('counts since Clear', () => {
    pgidStore.push(withHist({ '100': 5, '200': 2 }))
    act(() => pgidStore.clear())
    act(() => pgidStore.push(withHist({ '100': 8, '200': 2 })))
    render(<LatencyHistogram ids={[7]} />)
    expect(bars()).toHaveLength(1)
    expect(bars()[0]).toHaveTextContent('100–200 µs')
    expect(bars()[0]).toHaveTextContent('3')
  })
  it('renders "No samples yet" for an empty histogram', () => {
    pgidStore.push(withHist({}))
    render(<LatencyHistogram ids={[7]} />)
    expect(screen.getByText('No samples yet')).toBeInTheDocument()
    expect(bars()).toHaveLength(0)
  })
  it('shows the five error counters as labelled values', () => {
    const m = wirePgid('tx')
    Object.assign(m.latency['7'].err_cntrs, { dropped: 1, dup: 2, out_of_order: 3, seq_too_high: 4, seq_too_low: 5 })
    pgidStore.push(m)
    render(<LatencyHistogram ids={[7]} />)
    const dl = screen.getByLabelText('Error counters')
    for (const [label, v] of [['Dropped', '1'], ['Duplicates', '2'], ['Out of order', '3'], ['Seq too high', '4'], ['Seq too low', '5']]) {
      expect(within(dl).getByText(label).nextElementSibling).toHaveTextContent(new RegExp(`^${v}$`))
    }
  })
  it('chooses the pg_id', async () => {
    const m = wirePgid('tx')
    m.latency['8'] = JSON.parse(JSON.stringify(m.latency['7']))
    m.latency['8'].latency.histogram = { '300': 9 }
    pgidStore.push(m)
    render(<LatencyHistogram ids={[7, 8]} />)
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Histogram PG ID' }), '8')
    expect(bars()).toHaveLength(1)
    expect(bars()[0]).toHaveTextContent('300–400 µs')
  })
  it('bars use the accent token', () => {
    pgidStore.push(wirePgid('tx'))
    const { container } = render(<LatencyHistogram ids={[7]} />)
    const fills = [...container.querySelectorAll('rect')].map((r) => r.getAttribute('fill')).filter(Boolean)
    expect(fills.length).toBeGreaterThan(0)
    expect(fills.every((f) => f === 'var(--accent, #3b5bdb)')).toBe(true)
  })
})

describe('LatencyHistogram accessibility', () => {
  it('exposes the bars as list items with an accessible name, and hides the rects', () => {
    pgidStore.push(wirePgid('tx'))
    render(<LatencyHistogram ids={[7]} />)
    const list = screen.getByRole('list', { name: /Latency histogram of PG ID 7/ })
    expect(within(list).getAllByRole('listitem')).toHaveLength(11)
    expect(within(list).getByRole('listitem', { name: '100–200 µs: 74 packets' })).toBeInTheDocument()
    expect(list.querySelectorAll('rect:not([aria-hidden="true"])')).toHaveLength(0)
    expect(screen.queryByRole('img')).toBeNull()
  })
})

describe('LatencyHistogram bars selector', () => {
  const choose = (v: string) => userEvent.selectOptions(screen.getByRole('combobox', { name: 'Bars' }), v)
  it('defaults to 11; 17 and All show more', async () => {
    pgidStore.push(withHist(Object.fromEntries(Array.from({ length: 30 }, (_, i) => [String((i % 9 + 1) * 10 ** (Math.floor(i / 9) + 1)), 1]))))
    render(<LatencyHistogram ids={[7]} />)
    expect(screen.getByRole('combobox', { name: 'Bars' })).toHaveValue('11')
    expect(bars()).toHaveLength(11)
    await choose('17')
    expect(bars()).toHaveLength(17)
    await choose('All')
    expect(bars()).toHaveLength(30)
  })
  it('remembers the choice across a remount', async () => {
    pgidStore.push(wirePgid('tx'))
    const first = render(<LatencyHistogram ids={[7]} />)
    await choose('17')
    first.unmount()
    render(<LatencyHistogram ids={[7]} />)
    expect(screen.getByRole('combobox', { name: 'Bars' })).toHaveValue('17')
    expect(bars()).toHaveLength(17)
  })
  it('falls back to 11 for an unknown stored value', () => {
    localStorage.setItem('trex-web-gui.histogramBars', '5')
    pgidStore.push(wirePgid('tx'))
    render(<LatencyHistogram ids={[7]} />)
    expect(bars()).toHaveLength(11)
  })
  it('falls back to 11 when localStorage throws, and still switches', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied') })
    pgidStore.push(wirePgid('tx'))
    render(<LatencyHistogram ids={[7]} />)
    expect(bars()).toHaveLength(11)
    await choose('All')
    expect(bars()).toHaveLength(19)
    vi.restoreAllMocks()
  })
})

describe('bucketRange (time_histogram.cpp key scheme)', () => {
  it.each([[0, 0, 10], [10, 10, 20], [90, 90, 100], [100, 100, 200], [900, 900, 1000], [1000, 1000, 2000], [90000, 90000, 100000]])(
    'key %i covers %i to %i', (key, lo, hi) => { expect(bucketRange(key)).toEqual({ lo, hi }) })
  it('gives no upper bound at 10^6 or more, which the server cannot emit', () => {
    expect(bucketRange(1000000)).toEqual({ lo: 1000000, hi: null })
  })
  it('gives only a lower bound for a key the scheme cannot produce', () => {
    expect(bucketRange(150)).toEqual({ lo: 150, hi: null })
    expect(bucketRange(5)).toEqual({ lo: 5, hi: null })
  })
  it('labels such a key "≥ key µs"', () => {
    pgidStore.push(withHist({ '150': 2 }))
    render(<LatencyHistogram ids={[7]} />)
    expect(bars()[0]).toHaveTextContent('≥ 150 µs')
  })
  it('topBuckets takes the count, All meaning no limit', () => {
    const h = { '10': 1, '20': 1, '30': 1 }
    expect(topBuckets(h, 2).map((b) => b.key)).toEqual([20, 30])
    expect(topBuckets(h, 'All')).toHaveLength(3)
  })
  it('topBuckets is ascending', () => {
    expect(topBuckets({ '300': 1, '100': 2 }).map((b) => b.key)).toEqual([100, 300])
  })
})
