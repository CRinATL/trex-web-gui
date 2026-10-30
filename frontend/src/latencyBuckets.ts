export const HIST_BARS = 11                       // the default number of bars
export const BAR_CHOICES = [11, 17, 'All'] as const   // 17 is tui's max_histogram_size (trex_stl_stats.py)
export type BarChoice = (typeof BAR_CHOICES)[number]

// The range of packet latencies (µs) that one histogram key counts; hi is null where the server's scheme does not give it.
// The server (trex-core src/time_histogram.cpp, CTimeHistogram::Add and dump_json) keeps the leading decimal digit d of the
// latency at a decade 10^n (n >= 1) and reports the key d x 10^n, so the bucket is [key, key + 10^n): 10-19, 20-29 ... 90-99,
// 100-199, 200-299 ... The vendored client says the same (trex_stl_client.py, get_pgid_stats docstring: {100:13} is "between
// 100 and 200 usec"). The server cannot emit a key of 10^6 or more (HISTOGRAM_SIZE_LOG = 5 decades), so such a key has no
// upper bound here. Key 0 is the count of samples under m_min_delta (10 µs, time_histogram.cpp:81 and :117), so [0, 10).
export function bucketRange(key: number): { lo: number; hi: number | null } {
  if (key === 0) return { lo: 0, hi: 10 }
  const decade = 10 ** Math.floor(Math.log10(key))
  const digit = key / decade
  if (Number.isInteger(digit) && digit >= 1 && digit <= 9 && decade >= 10 && key < 1e6) return { lo: key, hi: key + decade }
  return { lo: key, hi: null }
}

// The n largest bucket keys with a nonzero count since Clear (all of them for 'All'), shown in ascending order.
export function topBuckets(hist: Record<string, number>, n: number | 'All' = HIST_BARS): { key: number; count: number }[] {
  return Object.entries(hist)
    .map(([k, count]) => ({ key: Number(k), count }))
    .filter((b) => Number.isFinite(b.key) && b.count > 0)
    .sort((a, b) => b.key - a.key)
    .slice(0, n === 'All' ? undefined : n)
    .sort((a, b) => a.key - b.key)
}

// A server histogram `[{key, val}, ...]` (CTimeHistogram::dump_json, time_histogram.cpp:234-271) as the key -> count record
// topBuckets and bucketRange read. Anything else (a missing list, a malformed entry) is skipped, never thrown on.
export function fromKeyVal(list: unknown): Record<string, number> {
  const out: Record<string, number> = {}
  if (!Array.isArray(list)) return out
  for (const e of list) {
    if (typeof e !== 'object' || e === null) continue
    const { key, val } = e as { key?: unknown; val?: unknown }
    if (typeof key === 'number' && Number.isFinite(key) && typeof val === 'number' && Number.isFinite(val)) out[String(key)] = val
  }
  return out
}
