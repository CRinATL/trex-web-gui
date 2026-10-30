import { useState, useSyncExternalStore } from 'react'
import { seriesById, SERIES } from '../chartSeries'
import { dashboardPrefs } from '../dashboardPrefs'
import { statsStore } from '../stats'
import { TimeChart } from './TimeChart'

type Props = { initialSeriesId: string; windowS: number; label: string }

// The total scope of one chosen statistic, over statsStore.
export function StatsChart({ initialSeriesId, windowS, label }: Props) {
  const [seriesId, setSeriesId] = useState(initialSeriesId)
  const { yFrom0 } = useSyncExternalStore(dashboardPrefs.subscribe, dashboardPrefs.get)
  const s = seriesById(seriesId)
  const data = (w: number): [number[], (number | null)[]] => {
    const pts = statsStore.series('total', w)
    return [pts.map((p) => p.ts), pts.map((p) => s.value(p))]
  }
  return (
    <TimeChart
      legend="line"
      yFrom0={yFrom0}
      title={s.label}
      subscribe={statsStore.subscribe}
      data={data}
      series={[{ label: s.label, color: 'var(--accent)' }]}
      format={s.format}
      windowS={windowS}
      latestTs={() => statsStore.latest()?.ts}
      caption={(
        <select aria-label={label} value={seriesId} onChange={(e) => setSeriesId(e.target.value)}>
          {SERIES.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
        </select>
      )}
    />
  )
}
