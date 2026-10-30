import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach, vi } from 'vitest'
import UPlot from 'uplot'
import { installMatchMedia, resetMatchMedia } from './testMatchMedia'
import { resetThemeStore } from './theme'
import { dashboardPrefs } from './dashboardPrefs'
import { monitorStore } from './monitorStore'
import { trafficStore } from './trafficStore'
import { runInProgress, setupSession } from './setupSession'

// Any test that renders DashboardPage would otherwise import the real uPlot. The mock records
// every constructor, setData, setSize and destroy call; tests read them from MockUPlot.instances.
vi.mock('uplot', async () => {
  // The real uPlot measures the pixel ratio with matchMedia when it loads, and jsdom has none yet.
  if (typeof window.matchMedia !== 'function') window.matchMedia = (() => ({ matches: false, addEventListener() {}, removeEventListener() {} })) as unknown as typeof window.matchMedia
  const real = await vi.importActual<{ default: { rangeNum: (...a: unknown[]) => unknown } }>('uplot')
  class MockUPlot {
    static instances: MockUPlot[] = []
    static rangeNum = real.default.rangeNum   // the real default y range, so tests pin it
    opts: unknown
    data: unknown
    target: HTMLElement
    setDataCalls: unknown[] = []
    setSizeCalls: { width: number; height: number }[] = []
    setSeriesCalls: unknown[][] = []
    destroyCalls = 0
    constructor(opts: unknown, data: unknown, target: HTMLElement) {
      this.opts = opts
      this.data = data
      this.target = target
      MockUPlot.instances.push(this)
    }
    setData(d: unknown) { this.setDataCalls.push(d); this.data = d }
    setSize(s: { width: number; height: number }) { this.setSizeCalls.push(s) }
    setSeries(...a: unknown[]) { this.setSeriesCalls.push(a) }
    destroy() { this.destroyCalls++ }
  }
  return { default: MockUPlot }
})

beforeEach(() => { (UPlot as unknown as { instances: unknown[] }).instances.length = 0; installMatchMedia() })
afterEach(() => { cleanup(); resetMatchMedia(); resetThemeStore(); trafficStore.reset(); monitorStore.reset(); dashboardPrefs.reset(); runInProgress.reset(); setupSession.reset() })
