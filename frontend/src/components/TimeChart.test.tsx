import { act, fireEvent, render, screen } from '@testing-library/react'
import UPlot from 'uplot'
import { describe, expect, it, vi } from 'vitest'
import { setPrefersDark } from '../testMatchMedia'
import { TimeChart } from './TimeChart'

type Mock = { instances: { setDataCalls: unknown[][]; setSeriesCalls: unknown[][]; destroyCalls: number; data: unknown; opts: unknown }[] }
const mock = UPlot as unknown as Mock
type Opts = { series: { label?: string; stroke?: string }[]; axes: { values?: (u: unknown, s: number[]) => string[] }[]; legend: { show: boolean }; hooks: { setCursor: ((u: unknown) => void)[] }; scales: { x: { range: (u: unknown, a: number, b: number) => [number, number] }; y: { range: (u: unknown, a: number | null, b: number | null) => [number, number] } } }
const opts = (i: number) => mock.instances[i].opts as Opts

function emitter() {
  const fns = new Set<() => void>()
  return { subscribe: (fn: () => void) => { fns.add(fn); return () => { fns.delete(fn) } }, emit: () => fns.forEach((f) => f()), size: () => fns.size }
}

const base = (e: ReturnType<typeof emitter>) => ({
  legend: 'line' as const, title: 't', subscribe: e.subscribe, data: () => [[1, 2], [3, 4]] as [number[], number[]],
  format: (v: number | null) => `<${v}>`, windowS: 60, latestTs: () => 100 as number | undefined,
})

describe('TimeChart', () => {
  it('is created once, setData runs on each notification, and unmount destroys it and unsubscribes', () => {
    const e = emitter()
    const props = { ...base(e), series: [{ label: 'a', color: '#111' }] }
    const { unmount, rerender } = render(<TimeChart {...props} />)
    expect(mock.instances).toHaveLength(1)
    const before = mock.instances[0].setDataCalls.length
    act(() => e.emit())
    act(() => e.emit())
    expect(mock.instances[0].setDataCalls.length).toBe(before + 2)
    rerender(<TimeChart {...props} data={() => [[1], [9]]} />)
    expect(mock.instances).toHaveLength(1)
    unmount()
    expect(mock.instances[0].destroyCalls).toBe(1)
    expect(e.size()).toBe(0)
  })
  it('is recreated when the series count changes', () => {
    const e = emitter()
    const { rerender } = render(<TimeChart {...base(e)} series={[{ label: 'a', color: '#111' }]} />)
    rerender(<TimeChart {...base(e)} series={[{ label: 'a', color: '#111' }, { label: 'b', color: '#222' }]} />)
    expect(mock.instances).toHaveLength(2)
    expect(mock.instances[0].destroyCalls).toBe(1)
    expect(opts(1).series.map((s) => s.label)).toEqual([undefined, 'a', 'b'])
    expect(e.size()).toBe(1)
  })
  it('same count, different pg_id: legend text updates', () => {
    const e = emitter()
    const { rerender } = render(<TimeChart {...base(e)} series={[{ label: 'pg 5', color: '#111' }]} />)
    rerender(<TimeChart {...base(e)} series={[{ label: 'pg 6', color: '#111' }]} />)
    expect(mock.instances).toHaveLength(2)
    expect(opts(1).series[1].label).toBe('pg 6')
  })
  it('same labels with a different colour is also a new chart', () => {
    const e = emitter()
    const { rerender } = render(<TimeChart {...base(e)} series={[{ label: 'a', color: '#111' }]} />)
    rerender(<TimeChart {...base(e)} series={[{ label: 'a', color: '#222' }]} />)
    expect(mock.instances).toHaveLength(2)
    expect(opts(1).series[1].stroke).toBe('#222')
  })
  it('a var() colour is resolved from the CSS variable, with the light-theme fallback', () => {
    const e = emitter()
    render(<TimeChart {...base(e)} series={[{ label: 'a', color: 'var(--accent)' }]} />)
    expect(opts(0).series[1].stroke).toBe('#3b5bdb')
  })
  it('x is pinned to the window ending at latestTs and follows a window change; data gets the window', () => {
    const e = emitter()
    const data = vi.fn(() => [[1], [2]] as [number[], number[]])
    const { rerender } = render(<TimeChart {...base(e)} data={data} series={[{ label: 'a', color: '#111' }]} />)
    expect(opts(0).scales.x.range(null, 0, 1)).toEqual([40, 100])
    rerender(<TimeChart {...base(e)} data={data} windowS={300} series={[{ label: 'a', color: '#111' }]} />)
    expect(opts(0).scales.x.range(null, 0, 1)).toEqual([-200, 100])
    expect(data).toHaveBeenLastCalledWith(300)
    expect(mock.instances).toHaveLength(1)
  })
  it('with no data yet the x range is uPlot\'s own; y ticks use format', () => {
    const e = emitter()
    render(<TimeChart {...base(e)} latestTs={() => undefined} series={[{ label: 'a', color: '#111' }]} />)
    expect(opts(0).scales.x.range(null, 5, 6)).toEqual([5, 6])
    expect(opts(0).axes[1].values?.(null, [1, 2])).toEqual(['<1>', '<2>'])
  })
  it('the y axis is as wide as its widest tick label, never under 80 px, so "400.00 kpps" is not clipped', () => {
    const e = emitter()
    render(<TimeChart {...base(e)} series={[{ label: 'a', color: '#111' }]} />)
    const size = (opts(0).axes[1] as unknown as { size: (u: unknown, v: string[] | null, i: number, c: number) => number }).size
    expect(typeof size).toBe('function')
    // a canvas at pixel ratio 2 whose font is 7 CSS px per character; ticks 10 px and gap 5 px, as uPlot's defaults
    const ctx = { font: '', measureText: (s: string) => ({ width: s.length * 7 * 2 }) }
    const self = { ctx, axes: [{}, { font: ['24px sans'], ticks: { show: true, size: 10 }, gap: 5 }] }
    UPlot.pxRatio = 2
    expect(size(self, ['0 pps', '200.00 kpps', '400.00 kpps'], 1, 1)).toBe(10 + 5 + 11 * 7)
    expect(ctx.font).toBe('24px sans')
    expect(size(self, ['0 pps', '2 pps'], 1, 1)).toBe(80)
    expect(size(self, null, 1, 0)).toBe(80)   // uPlot's first call, before any ticks exist
  })
  it('x ticks are one line: :ss, or the time at a whole minute, never a date (RC1-38)', () => {
    const e = emitter()
    render(<TimeChart {...base(e)} series={[{ label: 'a', color: '#111' }]} />)
    const at = (h: number, m: number, s: number) => new Date(2026, 9, 29, h, m, s).getTime() / 1000
    expect(opts(0).axes[0].values?.(null, [at(15, 45, 45), at(15, 46, 0), at(15, 46, 15), at(0, 5, 0), at(12, 0, 0)]))
      .toEqual([':45', '3:46pm', ':15', '12:05am', '12:00pm'])
  })
  it('does not rebuild on a data push', () => {
    const e = emitter()
    render(<TimeChart {...base(e)} series={[{ label: 'a', color: 'var(--accent)' }]} />)
    act(() => e.emit())
    expect(mock.instances).toHaveLength(1)
    expect(mock.instances[0].destroyCalls).toBe(0)
  })
  it('rebuilds when the resolved scheme changes under system', () => {
    const e = emitter()
    render(<TimeChart {...base(e)} series={[{ label: 'a', color: 'var(--accent)' }]} />)
    expect(mock.instances).toHaveLength(1)
    act(() => setPrefersDark(true))
    expect(mock.instances).toHaveLength(2)
    expect(mock.instances[0].destroyCalls).toBe(1)
    expect(mock.instances[1].destroyCalls).toBe(0)
  })

  describe('legend', () => {
    const one = [{ label: 'a', color: '#111' }]
    const three = [{ label: 'a', color: '#111' }, { label: 'b', color: '#222' }, { label: 'c', color: '#333' }]
    const x0 = new Date(2026, 9, 29, 10, 43, 52).getTime() / 1000
    const pps = (v: number | null) => `${v} pps`
    const lineData = () => [[x0 - 1, x0], [800, 845]] as [number[], number[]]
    const cursor = (idx: number | null) => act(() => opts(0).hooks.setCursor[0]({ cursor: { idx } }))

    it('uPlot is built with its own legend off', () => {
      render(<TimeChart {...base(emitter())} series={one} />)
      expect(opts(0).legend).toEqual({ show: false })
    })
    it('line: shows the latest value through format, and nothing without data', () => {
      const { unmount } = render(<TimeChart {...base(emitter())} data={lineData} format={pps} series={one} />)
      expect(screen.getByText('latest 845 pps')).toBeInTheDocument()
      expect(screen.queryByText(/Time|--/)).toBeNull()
      unmount()
      const { container } = render(<TimeChart {...base(emitter())} data={() => [[], []]} format={pps} series={one} />)
      expect(container.querySelector('.chart-legend')).toBeNull()
    })
    it('line: a cursor on index i shows its local time and value', () => {
      render(<TimeChart {...base(emitter())} data={lineData} format={pps} series={one} />)
      cursor(1)
      expect(screen.getByText('10:43:52 · 845 pps')).toBeInTheDocument()
      cursor(null)
      expect(screen.getByText('latest 845 pps')).toBeInTheDocument()
    })
    it('line: a cursor over a null point keeps the legend, reading no value', () => {
      render(<TimeChart {...base(emitter())} data={() => [[x0 - 1, x0], [null, 845]] as unknown as [number[], number[]]} format={pps} series={one} />)
      cursor(0)
      expect(screen.getByText('10:43:51 · no value')).toBeInTheDocument()
    })
    it('chips return to ✓ when the chart is rebuilt', () => {
      const e = emitter()
      const { rerender } = render(<TimeChart {...base(e)} legend="chips" series={three} />)
      fireEvent.click(screen.getAllByRole('button')[0])
      expect(screen.getAllByRole('button')[0]).toHaveAttribute('aria-pressed', 'false')
      rerender(<TimeChart {...base(e)} legend="chips" series={three.slice(0, 2)} />)
      expect(mock.instances).toHaveLength(2)
      screen.getAllByRole('button').forEach((c) => expect(c).toHaveAttribute('aria-pressed', 'true'))
    })
    it('chips: one pressed button per series, with ✓; clicking hides that series', () => {
      render(<TimeChart {...base(emitter())} legend="chips" series={three} />)
      const chips = screen.getAllByRole('button')
      expect(chips).toHaveLength(3)
      chips.forEach((c) => { expect(c).toHaveAttribute('aria-pressed', 'true'); expect(c).toHaveTextContent('✓') })
      fireEvent.click(chips[1])
      expect(chips[1]).toHaveAttribute('aria-pressed', 'false')
      expect(chips[1]).toHaveTextContent('✗')
      expect(mock.instances[0].setSeriesCalls).toEqual([[2, { show: false }]])
      fireEvent.click(chips[1])
      expect(mock.instances[0].setSeriesCalls[1]).toEqual([2, { show: true }])
    })
    it('the last shown series cannot be hidden', () => {
      render(<TimeChart {...base(emitter())} legend="chips" series={three} />)
      const chips = screen.getAllByRole('button')
      fireEvent.click(chips[0])
      fireEvent.click(chips[1])
      expect(chips[2]).toBeDisabled()
      expect(chips[2]).toHaveAttribute('title', expect.stringMatching(/last shown series/))
      expect(chips[0]).not.toBeDisabled()
    })
    it('chips: show each series value at the cursor', () => {
      render(<TimeChart {...base(emitter())} legend="chips" format={pps} data={() => [[x0 - 1, x0], [1, 2], [3, 4]]} series={one.concat(three[1])} />)
      expect(screen.getAllByRole('button')[1]).toHaveTextContent('b 4 pps')
      cursor(0)
      expect(screen.getAllByRole('button')[1]).toHaveTextContent('b 3 pps')
    })
    describe('hover pop-up', () => {
      // uPlot's setCursor hook with the cursor at (left, top) in the plot area, as uPlot reports it; left -10 is
      // where uPlot parks the cursor once the mouse leaves.
      const over = { clientWidth: 600, clientHeight: 200, getBoundingClientRect: () => ({ left: 0, top: 0 }) }
      const hoverAt = (idx: number | null, left: number, top: number) =>
        act(() => opts(0).hooks.setCursor[0]({ cursor: { idx, left, top }, over }))
      const tip = () => screen.queryByRole('tooltip')

      it('uPlot draws only the vertical cursor line, with its point on each series', () => {
        render(<TimeChart {...base(emitter())} series={one} />)
        expect((mock.instances[0].opts as { cursor: unknown }).cursor).toEqual({ y: false })
      })
      it('shows the time, the series and its value beside the cursor, and goes when the mouse leaves', () => {
        render(<TimeChart {...base(emitter())} data={lineData} format={pps} series={one} />)
        expect(tip()).toBeNull()
        hoverAt(1, 100, 50)
        expect(tip()).toHaveTextContent('10:43:52')
        expect(tip()).toHaveTextContent('a845 pps')
        expect(tip()).toHaveStyle({ left: '112px', top: '62px' })
        hoverAt(null, -10, -10)
        expect(tip()).toBeNull()
      })
      it('reads no value over a missing point', () => {
        render(<TimeChart {...base(emitter())} data={() => [[x0 - 1, x0], [null, 845]] as unknown as [number[], number[]]} format={pps} series={one} />)
        hoverAt(0, 100, 50)
        expect(tip()).toHaveTextContent('ano value')
      })
      it('flips to the cursor\'s left near the right edge and above it near the bottom', () => {
        render(<TimeChart {...base(emitter())} data={lineData} format={pps} series={one} />)
        hoverAt(1, 580, 190)
        const style = (tip() as HTMLElement).style
        expect(parseFloat(style.left)).toBeLessThan(580)
        expect(parseFloat(style.top)).toBeLessThan(190)
      })
      it('chips: lists each shown series with its value at the cursor, not a hidden one', () => {
        render(<TimeChart {...base(emitter())} legend="chips" format={pps} data={() => [[x0 - 1, x0], [1, 2], [3, 4], [5, 6]]} series={three} />)
        fireEvent.click(screen.getAllByRole('button')[1])
        hoverAt(0, 100, 50)
        expect(tip()).toHaveTextContent('a1 pps')
        expect(tip()).not.toHaveTextContent('b3 pps')
        expect(tip()).toHaveTextContent('c5 pps')
      })
    })
    it('yFrom0: the y range starts at 0, [0, 1] without a positive max; toggling redraws without rebuilding', () => {
      const props = { ...base(emitter()), series: one }
      const { rerender } = render(<TimeChart {...props} />)
      expect(opts(0).scales.y.range(null, 0, 0).map((v) => v + 0)).toEqual([0, 100])   // uPlot yields -0 for the min
      expect(opts(0).scales.y.range(null, 845, 845)).toEqual([0, 1690])
      const [nlo, nhi] = opts(0).scales.y.range(null, -3, 0)
      expect(nlo).toBeCloseTo(-3.3, 9)
      expect(nhi + 0).toBe(0)
      expect(opts(0).scales.y.range(null, null, null)).toEqual([null, null])
      const before = mock.instances[0].setDataCalls.length
      rerender(<TimeChart {...props} yFrom0 />)
      const [lo, hi] = opts(0).scales.y.range(null, 5, 9)
      expect(lo).toBe(0)
      expect(hi).toBeGreaterThan(9)   // headroom above the peak, as Fit has
      expect(opts(0).scales.y.range(null, -3, 0)).toEqual([0, 1])
      expect(opts(0).scales.y.range(null, null, null)).toEqual([0, 1])
      expect(mock.instances).toHaveLength(1)
      expect(mock.instances[0].setDataCalls.length).toBeGreaterThan(before)
    })
  })
})
