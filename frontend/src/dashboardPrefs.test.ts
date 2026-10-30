import { afterEach, describe, expect, it, vi } from 'vitest'
import { DEFAULT_INTERVAL, DEFAULT_LAYOUT } from './chartSeries'
import { dashboardPrefs } from './dashboardPrefs'

afterEach(() => dashboardPrefs.reset())

describe('dashboardPrefs', () => {
  it('get returns the defaults', () => {
    expect(dashboardPrefs.get()).toEqual({ tab: 'overview', windowS: DEFAULT_INTERVAL, layout: DEFAULT_LAYOUT, yFrom0: false })
    expect(dashboardPrefs.get()).toBe(dashboardPrefs.get())
  })
  it('set patches one field only', () => {
    dashboardPrefs.set({ tab: 'latency' })
    expect(dashboardPrefs.get()).toEqual({ tab: 'latency', windowS: DEFAULT_INTERVAL, layout: DEFAULT_LAYOUT, yFrom0: false })
  })
  it('reset clears', () => {
    dashboardPrefs.set({ tab: 'streams', windowS: 300, layout: 4 })
    dashboardPrefs.reset()
    expect(dashboardPrefs.get()).toEqual({ tab: 'overview', windowS: DEFAULT_INTERVAL, layout: DEFAULT_LAYOUT, yFrom0: false })
  })
  it('yFrom0 defaults to Fit, is patched alone, and reset returns to Fit', () => {
    dashboardPrefs.set({ yFrom0: true })
    expect(dashboardPrefs.get()).toEqual({ tab: 'overview', windowS: DEFAULT_INTERVAL, layout: DEFAULT_LAYOUT, yFrom0: true })
    dashboardPrefs.reset()
    expect(dashboardPrefs.get().yFrom0).toBe(false)
  })
  it('subscribe notifies', () => {
    const fn = vi.fn()
    const off = dashboardPrefs.subscribe(fn)
    dashboardPrefs.set({ layout: 1 })
    dashboardPrefs.reset()
    expect(fn).toHaveBeenCalledTimes(2)
    off()
    dashboardPrefs.set({ layout: 4 })
    expect(fn).toHaveBeenCalledTimes(2)
  })
})
