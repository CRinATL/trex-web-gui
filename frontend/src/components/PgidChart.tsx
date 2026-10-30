import { useState, useSyncExternalStore } from 'react'
import { INTERVALS } from '../chartSeries'
import { dashboardPrefs } from '../dashboardPrefs'
import { CAP_REASON, capReasonId } from '../pgidPalette'
import { pgidStore, type SeriesKey, type Tab } from '../pgidStats'
import type { Selection } from './PgidSelector'
import { TimeChart } from './TimeChart'
import { YAxisToggle } from './YAxisToggle'

export type Metric = { id: SeriesKey; label: string; format: (v: number | null) => string }

// A table row's "Chart PG ID n" checkbox, disabled with a reason once the selection is full.
export function ChartCheckbox({ tab, id, sel }: { tab: Tab; id: number; sel: Selection }) {
  const on = sel.isSelected(id)
  const blocked = !on && sel.atCap
  return (
    <input type="checkbox" aria-label={`Chart PG ID ${id}`} checked={on} disabled={blocked} title={blocked ? CAP_REASON : undefined}
      aria-describedby={blocked ? capReasonId(tab) : undefined} onChange={() => sel.toggle(id)} />
  )
}

// The chart of one tab: a metric picker and an interval picker over a TimeChart with a series per selected pg_id.
// A new metric is passed as dataKey, so the chart redraws at once without a remount (the pickers keep keyboard focus).
export function PgidChart({ title, metrics, metricLabel, intervalLabel, sel, windowS, setWindowS }: {
  title: (m: Metric) => string
  metrics: Metric[]
  metricLabel: string
  intervalLabel: string
  sel: Selection
  windowS: number
  setWindowS: (n: number) => void
}) {
  const { yFrom0 } = useSyncExternalStore(dashboardPrefs.subscribe, dashboardPrefs.get)
  const [metric, setMetric] = useState<SeriesKey>(metrics[0].id)
  const m = metrics.find((x) => x.id === metric) ?? metrics[0]
  // Series share the message timestamps but a newly picked pg_id starts later, so the x axis is the union of their points.
  const data = (w: number): [number[], ...(number | null)[][]] => {
    const per = sel.selected.map((s) => pgidStore.series(s.id, m.id, w))
    const xs = [...new Set(per.flatMap((pts) => pts.map((p) => p.ts)))].sort((a, b) => a - b)
    return [xs, ...per.map((pts) => { const by = new Map(pts.map((p) => [p.ts, p.v])); return xs.map((x) => by.get(x) ?? null) })]
  }
  return (
    <TimeChart
      legend="chips"
      yFrom0={yFrom0}
      dataKey={m.id}
      title={title(m)}
      subscribe={pgidStore.subscribe}
      data={data}
      series={sel.selected.map((s) => ({ label: `PG ${s.id}`, color: s.color }))}
      format={m.format}
      windowS={windowS}
      latestTs={() => pgidStore.latest()?.ts}
      controls={(
        <>
          <label>
            Metric
            <select aria-label={metricLabel} value={m.id} onChange={(e) => setMetric(e.target.value as SeriesKey)}>
              {metrics.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
            </select>
          </label>
          <label>
            Interval
            <select aria-label={intervalLabel} value={windowS} onChange={(e) => setWindowS(Number(e.target.value))}>
              {INTERVALS.map((n) => <option key={n} value={n}>{n} s</option>)}
            </select>
          </label>
          <YAxisToggle />
        </>
      )}
    />
  )
}
