import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { DEFAULT_INTERVAL } from './chartSeries'
import { DASH_VIEW_DEFAULTS, DASH_VIEW_KEY, readDashView, resetDashViewStore, useDashView } from './dashView'
import { dashboardPrefs } from './dashboardPrefs'

const store = (v: unknown) => localStorage.setItem(DASH_VIEW_KEY, typeof v === 'string' ? v : JSON.stringify(v))

describe('dashView', () => {
  beforeEach(() => { localStorage.clear(); resetDashViewStore() })

  it('reads the defaults with nothing stored', () => {
    expect(readDashView()).toEqual(DASH_VIEW_DEFAULTS)
    expect(DASH_VIEW_DEFAULTS.overviewSeries).toEqual(['tx_pps', 'rx_pps', 'tx_bps_l2', 'rx_bps_l2'])
  })

  it('keeps each valid field and defaults each bad one', () => {
    store({ bitRates: true, counters: 'yes', wide: 1, streamsMetric: 'rx_pps', latencyMetric: 'nope', streamsWindowS: 90, latencyWindowS: 45 })
    expect(readDashView()).toMatchObject({
      bitRates: true, counters: false, wide: false, streamsMetric: 'rx_pps', latencyMetric: 'lat_avg',
      streamsWindowS: 90, latencyWindowS: DEFAULT_INTERVAL,
    })
  })

  it('defaults one bad overview slot', () => {
    store({ overviewSeries: ['tx_bps_l1', 'gone', 'tx_pps', 'rx_pps'] })
    expect(readDashView().overviewSeries).toEqual(['tx_bps_l1', 'rx_pps', 'tx_pps', 'rx_pps'])
    store({ overviewSeries: ['tx_pps', 'rx_pps', 'tx_pps'] })
    expect(readDashView().overviewSeries).toEqual(DASH_VIEW_DEFAULTS.overviewSeries)
  })

  it('survives unparsable or non-object storage', () => {
    for (const s of ['{', '[]', 'null', '7']) {
      store(s)
      expect(readDashView()).toEqual(DASH_VIEW_DEFAULTS)
    }
  })

  it('survives storage that throws', () => {
    const get = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied') })
    expect(readDashView()).toEqual(DASH_VIEW_DEFAULTS)
    get.mockRestore()
    const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('full') })
    const { result } = renderHook(useDashView)
    act(() => result.current.setView({ wide: true }))
    expect(result.current.view.wide).toBe(true)
    set.mockRestore()
  })

  it('setView merges, saves and notifies', () => {
    const { result } = renderHook(useDashView)
    act(() => result.current.setView({ wide: true }))
    expect(result.current.view).toEqual({ ...DASH_VIEW_DEFAULTS, wide: true })
    expect(JSON.parse(localStorage.getItem(DASH_VIEW_KEY)!).wide).toBe(true)
  })

  it('is not reset by a new connection', () => {
    const { result } = renderHook(useDashView)
    act(() => result.current.setView({ counters: true }))
    dashboardPrefs.reset()
    resetDashViewStore()
    expect(readDashView().counters).toBe(true)
  })
})
