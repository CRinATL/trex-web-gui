import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import UPlot from 'uplot'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { statsFromSample } from '../testStats'
import { statsStore } from '../stats'
import { StatsChart } from './StatsChart'
import { DashboardPage } from '../pages/DashboardPage'

type Mock = { instances: { setDataCalls: unknown[][]; destroyCalls: number; data: unknown[][]; opts: unknown; target: HTMLElement }[] }
const mock = UPlot as unknown as Mock
// The dashboard's Streams tab keeps a chart of its own mounted; these tests count the Overview's.
const overviewCharts = () => mock.instances.filter((i) => i.target.closest('#dash-panel-overview'))

const reset = () => { statsStore.onSession('connected'); statsStore.onSession('disconnected') }
beforeEach(reset)
afterEach(() => { reset(); vi.restoreAllMocks() })

describe('StatsChart', () => {
  it('uPlot is created once and destroyed on unmount', () => {
    const { unmount } = render(<StatsChart initialSeriesId="tx_pps" windowS={60} label="Chart 1 series" />)
    expect(mock.instances).toHaveLength(1)
    act(() => statsStore.push(statsFromSample(3)))
    act(() => statsStore.push(statsFromSample(3)))
    expect(mock.instances).toHaveLength(1)
    expect(mock.instances[0].setDataCalls.length).toBeGreaterThanOrEqual(2)
    expect(mock.instances[0].destroyCalls).toBe(0)
    unmount()
    expect(mock.instances[0].destroyCalls).toBe(1)
  })

  it('plots the total scope: x is ts, y is the chosen field', () => {
    const base = statsFromSample(3)
    statsStore.push({ ...base, total: { tx_pps: 5, rx_pps: 6, tx_bps: 70, rx_bps: 80, tx_bps_L1: 90, rx_bps_L1: 100 } })
    render(<StatsChart initialSeriesId="tx_bps_l2" windowS={60} label="Chart 1 series" />)
    expect(mock.instances[0].data).toEqual([[base.ts], [70]])
  })

  it('the interval selector asks series() for the right window', async () => {
    const spy = vi.spyOn(statsStore, 'series')
    render(<DashboardPage />)
    expect(spy).toHaveBeenCalledWith('total', 60)
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Interval' }), '300')
    expect(spy).toHaveBeenCalledWith('total', 300)
  })

  it('layout 2 shows two charts by default and 4 shows four', async () => {
    render(<DashboardPage />)
    expect(overviewCharts()).toHaveLength(2)
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Layout' }), '4')
    expect(screen.getAllByRole('combobox', { name: /Chart \d series/ })).toHaveLength(4)
  })

  it('axes and series use the CSS variables, with a 80 px y axis formatted by unit', () => {
    vi.spyOn(window, 'getComputedStyle').mockReturnValue({
      getPropertyValue: (n: string) => ({ '--fg': '#eee', '--border': '#333', '--chart-grid': '#444', '--accent': '#8af' })[n] ?? '',
    } as unknown as CSSStyleDeclaration)
    render(<StatsChart initialSeriesId="tx_bps_l2" windowS={60} label="Chart 1 series" />)
    const o = mock.instances[0].opts as {
      axes: { stroke: string; grid: { stroke: string }; ticks: { stroke: string }; size?: number; values?: (u: unknown, s: number[]) => string[] }[]
      series: { stroke: string }[]
    }
    for (const a of o.axes) expect([a.stroke, a.grid.stroke, a.ticks.stroke]).toEqual(['#eee', '#444', '#333'])
    expect(o.series[1].stroke).toBe('#8af')
    expect(o.axes[1].size).toBeGreaterThanOrEqual(80)
    expect(o.axes[1].values?.(null, [1.5e9])).toEqual(['1.50 Gbps'])
  })

  it('a pps series formats ticks as pps', () => {
    render(<StatsChart initialSeriesId="rx_pps" windowS={60} label="Chart 1 series" />)
    const o = mock.instances[0].opts as { axes: { values?: (u: unknown, s: number[]) => string[] }[] }
    expect(o.axes[1].values?.(null, [1.5e9])).toEqual(['1.50 Gpps'])
  })

  it('the x range spans the interval ending at the newest ts', async () => {
    render(<DashboardPage />)
    const m = statsFromSample(3)
    act(() => statsStore.push(m))
    const x = (mock.instances[0].opts as { scales: { x: { range: (u: unknown, a: number, b: number) => [number, number] } } }).scales.x
    expect(x.range(null, 0, 1)).toEqual([m.ts - 60, m.ts])
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Interval' }), '300')
    expect(x.range(null, 0, 1)).toEqual([m.ts - 300, m.ts])
  })

  it('after an interval change the subscribe path asks series() for the new window; no chart is recreated', async () => {
    render(<DashboardPage />)
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Interval' }), '300')
    expect(overviewCharts()).toHaveLength(2)
    const spy = vi.spyOn(statsStore, 'series')
    act(() => statsStore.push(statsFromSample(3)))
    expect(spy).toHaveBeenCalledWith('total', 300)
    expect(spy).not.toHaveBeenCalledWith('total', 60)
    expect(overviewCharts()).toHaveLength(2)
  })
})

describe('StatsChart on TimeChart', () => {
  it('the series select is the chart caption', () => {
    render(<StatsChart initialSeriesId="tx_pps" windowS={60} label="Chart 1 series" />)
    const cap = screen.getByRole('combobox', { name: 'Chart 1 series' }).closest('figcaption')
    expect(cap).not.toBeNull()
    expect(screen.getByRole('figure', { name: 'Tx pps' })).toContainElement(cap)
  })
  it('choosing another series rebuilds the chart with that series label', async () => {
    render(<StatsChart initialSeriesId="tx_pps" windowS={60} label="Chart 1 series" />)
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Chart 1 series' }), 'rx_pps')
    expect(mock.instances).toHaveLength(2)
    expect(mock.instances[0].destroyCalls).toBe(1)
    expect((mock.instances[1].opts as { series: { label?: string }[] }).series[1].label).toBe('Rx pps')
  })
})
