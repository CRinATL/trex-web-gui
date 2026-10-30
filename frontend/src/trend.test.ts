import { describe, expect, it } from 'vitest'
import { trend, trendLevel } from './trend'

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
})
