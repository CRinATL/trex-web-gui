import { useState } from 'react'
import { pgidStore, type ErrCounter } from '../pgidStats'
import { BAR_CHOICES, HIST_BARS, topBuckets, type BarChoice } from '../latencyBuckets'
import { cell } from '../statsFormat'
import { HistogramSvg } from './HistogramSvg'

const ERRORS: { id: ErrCounter; label: string }[] = [
  { id: 'dropped', label: 'Dropped' }, { id: 'dup', label: 'Duplicates' }, { id: 'out_of_order', label: 'Out of order' },
  { id: 'seq_too_high', label: 'Seq too high' }, { id: 'seq_too_low', label: 'Seq too low' },
]

const BARS_KEY = 'trex-web-gui.histogramBars'
const readBars = (): BarChoice => {
  try {
    const v = localStorage.getItem(BARS_KEY)
    return BAR_CHOICES.find((c) => String(c) === v) ?? HIST_BARS
  } catch { return HIST_BARS }
}
const saveBars = (c: BarChoice) => { try { localStorage.setItem(BARS_KEY, String(c)) } catch { /* storage unavailable: the choice lasts until reload */ } }

// One latency pg_id's histogram as an SVG, with its five error counters beside it.
// chosen / onChoose: the pg_id lives in the caller, which stays mounted while another dashboard tab is shown.
export function LatencyHistogram({ ids, chosen, onChoose }: { ids: number[]; chosen: number | null; onChoose: (id: number) => void }) {
  const [count, setCount] = useState<BarChoice>(readBars)
  const id = chosen !== null && ids.includes(chosen) ? chosen : ids[0]
  const bars = id === undefined ? [] : topBuckets(pgidStore.relativeHist(id), count)
  return (
    <section aria-label="Latency histogram">
      <h3>Latency histogram</h3>
      <label>
        PG ID
        <select aria-label="Histogram PG ID" value={id ?? ''} onChange={(e) => onChoose(Number(e.target.value))}>
          {ids.map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
      </label>
      <label>
        Bars
        <select aria-label="Bars" value={String(count)} onChange={(e) => { const c = BAR_CHOICES.find((x) => String(x) === e.target.value) ?? HIST_BARS; setCount(c); saveBars(c) }}>
          {BAR_CHOICES.map((c) => <option key={c} value={String(c)}>{c}</option>)}
        </select>
      </label>
      <HistogramSvg bars={bars} scale="linear" label={`Latency histogram of PG ID ${id}, packets per latency range`} />
      <dl aria-label="Error counters">
        {ERRORS.map((e) => (
          <div key={e.id}><dt>{e.label}</dt><dd>{cell(id === undefined ? null : pgidStore.relativeErr(id, e.id))}</dd></div>
        ))}
      </dl>
    </section>
  )
}
