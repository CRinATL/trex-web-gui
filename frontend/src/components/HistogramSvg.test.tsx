import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { fromKeyVal } from '../latencyBuckets'
import { HistogramSvg } from './HistogramSvg'

const BARS = [{ key: 100, count: 1 }, { key: 200, count: 10000 }]
const widths = (scale: 'linear' | 'log') => {
  const { unmount } = render(<HistogramSvg bars={BARS} label="h" scale={scale} />)
  const w = screen.getAllByTestId('hist-bar').map((g) => Number(g.querySelector('rect')!.getAttribute('width')))
  unmount()
  return w
}

describe('HistogramSvg', () => {
  it('log scale narrows the ratio', () => {
    const [lin0, lin1] = widths('linear'), [log0, log1] = widths('log')
    expect(lin1 / lin0).toBeGreaterThan(100)   // the 1-count bar is held at the 1 px minimum
    expect(log1 / log0).toBeLessThan(20)
    expect(log1).toBeGreaterThan(log0)
  })

  it('the largest bar is full width on both scales, even for small counts', () => {
    for (const scale of ['linear', 'log'] as const) {
      const { unmount } = render(<HistogramSvg bars={[{ key: 100, count: 1 }, { key: 200, count: 2 }]} label="h" scale={scale} />)
      const w = screen.getAllByTestId('hist-bar').map((g) => Number(g.querySelector('rect')!.getAttribute('width')))
      expect(w[1]).toBe(260)
      expect(w[0]).toBeLessThan(260)
      unmount()
    }
  })

  it('says so with no bars', () => {
    render(<HistogramSvg bars={[]} label="h" scale="linear" />)
    expect(screen.getByText('No samples yet')).toBeInTheDocument()
  })

  it('fromKeyVal reads the server list and skips malformed entries', () => {
    expect(fromKeyVal([{ key: 20, val: 1 }, { key: 100, val: 7 }, null, { key: 'x', val: 1 }, { key: 5 }])).toEqual({ 20: 1, 100: 7 })
    expect(fromKeyVal(undefined)).toEqual({})
  })
})
