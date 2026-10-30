import { useSyncExternalStore } from 'react'
import { dashboardPrefs } from '../dashboardPrefs'
import { PGID_PALETTE } from '../pgidPalette'
import { cell } from '../statsFormat'
import { utilStore } from '../utilStats'
import { CHART_CORES, CPU_WINDOW_S, coreLabel, cpuAvg, portsText } from '../utilTables'
import { TimeChart } from './TimeChart'
import { YAxisToggle } from './YAxisToggle'

// The CPU table of the Utilization tab, as to_table_cpu (the tui source) computes it: Avg of the newest 5 samples and
// Latest, which is history[0].
export function CpuTable() {
  useSyncExternalStore(utilStore.subscribe, utilStore.version)
  const msg = utilStore.latest()
  if (!msg) return <p>No utilization data yet.</p>
  return (
    <table className="dash-table" aria-label="CPU utilization">
      <thead>
        <tr><th scope="col">Core</th><th scope="col">Ports</th><th scope="col" title="mean of the newest 5 samples, rounded half to even">Avg (%)</th><th scope="col">Latest (%)</th></tr>
      </thead>
      <tbody>
        {msg.cpu.map((c, i) => (
          <tr key={i}>
            <th scope="row">Core {i}</th>
            <td>{portsText(c)}</td>
            <td>{cell(cpuAvg(c.history))}</td>
            <td>{cell(c.history[0])}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

const percent = (v: number | null) => (v !== null && Number.isFinite(v) ? `${Math.round(v)}%` : '–')

// The CPU chart over 300 s: a series per core, the first 8 at most. Mounted even while the tab is hidden, so the history
// the store keeps is not redrawn from scratch on each show.
export function CpuChart() {
  useSyncExternalStore(utilStore.subscribe, utilStore.version)
  const { yFrom0 } = useSyncExternalStore(dashboardPrefs.subscribe, dashboardPrefs.get)
  const cores = utilStore.latest()?.cpu ?? []
  const shown = cores.slice(0, CHART_CORES)
  const data = (w: number): [number[], ...(number | null)[][]] => {
    const per = shown.map((_, i) => utilStore.cpuSeries(i, w))
    const xs = [...new Set(per.flatMap((pts) => pts.map((p) => p.ts)))].sort((a, b) => a - b)
    return [xs, ...per.map((pts) => { const by = new Map(pts.map((p) => [p.ts, p.v])); return xs.map((x) => by.get(x) ?? null) })]
  }
  return (
    <>
      <TimeChart
        legend="chips"
        yFrom0={yFrom0}
        controls={<YAxisToggle />}
        title="CPU utilization over time"
        subscribe={utilStore.subscribe}
        data={data}
        series={shown.map((c, i) => ({ label: coreLabel(i, c), color: PGID_PALETTE[i] }))}
        format={percent}
        windowS={CPU_WINDOW_S}
        latestTs={() => utilStore.latest()?.ts}
      />
      {cores.length > CHART_CORES && <p>Charting the first {CHART_CORES} of {cores.length} cores.</p>}
    </>
  )
}
