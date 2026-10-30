import type { StatsMsg } from './api'
import summary from '../../tests/fixtures/p3/summary.json'

type Rec = Record<string, number | null>

// A StatsMsg built from the Task 1 recording: sample i's global and port records. The sample has no `total`, so total
// is empty unless `over.total` supplies it.
// 0 and 1 are idle, 2-4 are under load, 5 and 6 are after stop.
export function statsFromSample(i: number, over: { ts?: number; interval_s?: number; global?: Rec; ports?: Record<string, Rec>; total?: Rec } = {}): StatsMsg {
  const s = summary.samples[i].stats as unknown as { global: Rec; total: Rec } & Record<string, Rec>
  return {
    type: 'stats',
    ts: over.ts ?? Date.now() / 1000,
    interval_s: over.interval_s ?? 1,
    global: { ...s.global, ...over.global },
    ports: over.ports ?? { '0': s['0'], '1': s['1'] },
    total: { ...s.total, ...over.total },
  }
}
