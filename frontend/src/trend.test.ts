import { describe, expect, it } from 'vitest'
import { HOLD, steadyTrend, trend, trendLevel } from './trend'

// Expected values come from the vendored calculate_diff (backend/trex_web_gui/_vendor/trex/common/stats/trex_stats.py),
// run on each sample set: see the task-5 report for the command.
describe('trend', () => {
  it('matches calculate_diff', () => {
    expect(trend([100, 110, 121, 133.1, 146.41])).toBeCloseTo(10.000000000000002, 10)
    expect(trend([1000, 900, 810, 700, 500])).toBeCloseTo(-18.502645502645507, 10)
    expect(trend([0, 50, 100, 100, 100])).toBeCloseTo(30.0, 10)    // a 0 counts as 1, and each step is capped at +100%
    expect(trend([10, 10.5, 10.2, 10.9, 10.4])).toBeCloseTo(0.15253257266209896, 10)
  })
  it('uses the last 5 samples, and is 0 with fewer than 5 or when the latest is below 10', () => {
    expect(trend([7, 7, 100, 110, 121, 133.1, 146.41])).toBeCloseTo(10, 10)
    expect(trend([100, 110, 121, 133.1])).toBe(0)
    expect(trend([])).toBe(0)
    expect(trend([100, 110, 121, 133.1, 9.99])).toBe(0)
    expect(trend([100, 110, 121, 133.1, 10])).not.toBe(0)
  })
  it('the trendLevel thresholds', () => {
    const cases: [number, number][] = [[0, 0], [0.99, 0], [-0.99, 0], [1, 1], [2, 1], [2.01, 2], [5, 2], [5.01, 3], [-1, -1], [-2, -1], [-2.01, -2], [-5, -2], [-5.01, -3], [100, 3]]
    for (const [v, level] of cases) expect(trendLevel(v), String(v)).toBe(level)
  })
  describe('steadyTrend', () => {
    const rising = [1000, 1100, 1210, 1331, 1464, 1611, 1772]   // +10 % a step: trend() is 10 at each of the last 3
    it('shows a direction once the last HOLD trends agree, with the weakest level of them and the latest %', () => {
      expect(HOLD).toBe(3)
      expect(steadyTrend(rising, 100)).toEqual({ level: 3, pct: trend(rising) })
      const falling = [...rising].reverse()
      expect(steadyTrend(falling, 100).level).toBe(-3)
    })
    it('needs 4 + HOLD samples, so 6 show nothing', () => {
      expect(steadyTrend(rising.slice(1), 100).level).toBe(0)
    })
    it('one disagreeing trend in the last HOLD hides it: a turn shows only once it holds', () => {
      // a ramp that turns: the trends end +, -, - (levels 3, -3, -3), so the new direction has not held yet
      const turned = [1000, 1100, 1210, 1331, 1464, 1000, 1000]
      expect([5, 6, 7].map((k) => trendLevel(trend(turned.slice(0, k))))).toEqual([3, -3, -3])
      expect(steadyTrend(turned, 100).level).toBe(0)
      // TUI's maths, kept: alternating noise reads as rising (+100 % up, -50 % down), so the floor, not HOLD, hides it
      expect(steadyTrend([1000, 2000, 1000, 2000, 1000, 2000, 1000], 100).level).toBe(3)
      // a ramp that drops on the last sample: the newest trend turns negative while the two before are positive
      const dropped = [...rising.slice(0, 6), 1000]
      expect(trend(dropped)).toBeLessThan(0)
      expect(steadyTrend(dropped, 100).level).toBe(0)
    })
    it('the weight is the weakest of the HOLD levels', () => {
      // one +9 % step ageing out of the window: trends 3.6, 2.7 then 1.8 %, levels 2, 2, 1, so the weakest (1) shows
      const s = [1000, 1000, 1000, 1000, 1090, 1090, 1090]
      expect([5, 6, 7].map((k) => trendLevel(trend(s.slice(0, k))))).toEqual([2, 2, 1])
      expect(steadyTrend(s, 100).level).toBe(1)
    })
    it('a latest rate under the floor shows nothing, whatever the trend', () => {
      expect(steadyTrend(rising, 1773).level).toBe(0)
      expect(steadyTrend(rising, 1772).level).toBe(3)
    })
  })
})
