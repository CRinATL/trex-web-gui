// Ports calculate_diff and get_trend_gui (backend/trex_web_gui/_vendor/trex/common/stats/trex_stats.py) so the GUI's
// arrows agree with the TUI's.

// samples: oldest first. A weighted mean of the step-to-step change in percent, newer steps weighing more;
// a 0 counts as 1 and each step is capped at +100%.
function calculateDiff(samples: number[]): number {
  const n = samples.length
  const step = 1 / ((n * (n - 1)) / 2)
  let weight = step
  let total = 0
  for (let i = 0; i < n - 1; i++) {
    const current = samples[i] > 0 ? samples[i] : 1
    const next = samples[i + 1] > 0 ? samples[i + 1] : 1
    total += Math.min(100 * (next / current - 1), 100) * weight
    weight += step
  }
  return total
}

// The last 5 samples, oldest first. 0 with fewer than 5 samples, or when the latest is below 10 (noise).
export function trend(samples: number[]): number {
  if (samples.length < 5) return 0
  if (samples[samples.length - 1] < 10) return 0
  return calculateDiff(samples.slice(-5))
}

// -3..3: 0 inside 1%, then one, two or three arrows above 1%, 2% and 5%.
export function trendLevel(v: number): number {
  const a = Math.abs(v)
  const level = a < 1 ? 0 : a > 5 ? 3 : a > 2 ? 2 : 1
  return v < 0 && level !== 0 ? -level : level
}
