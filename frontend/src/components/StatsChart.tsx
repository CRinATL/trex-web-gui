import { useSyncExternalStore } from 'react'
import { seriesById, SERIES } from '../chartSeries'
import { dashboardPrefs } from '../dashboardPrefs'
import { useDashView } from '../dashView'
import { statsStore } from '../stats'
import { TimeChart } from './TimeChart'

type Props = { index: number; windowS: number; label: string }

// The total scope of one chosen statistic, over statsStore. The choice for chart `index` lives in dashView, so it survives leaving the page.
export function StatsChart({ index, windowS, label }: Props) {
  const { view, setView } = useDashView()
  const seriesId = view.overviewSeries[index]
  const setSeriesId = (id: string) => setView({ overviewSeries: view.overviewSeries.map((o, i) => (i === index ? id : o)) })
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
