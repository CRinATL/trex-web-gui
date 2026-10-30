import { useMemo } from 'react'
import { useMonitor, monitorStore, type MonitorHistory } from '../../monitorStore'
import { globalTiles, type TileGroupName, type TileKind, type TileSpec } from '../../monitorLayouts'
import { cell, humanBps, humanPps } from '../../statsFormat'
import { Tile, TileGroup } from '../Tile'
import { TimeChart } from '../TimeChart'

const GROUPS: TileGroupName[] = ['Tx', 'Rx', 'Flows', 'System']
const CHART_WINDOW_S = 150   // 300 points at the publisher's 2 Hz ceiling

const pct = (v: number | null) => (cell(v) === '–' ? '–' : `${cell(v)} %`)
function formatKind(kind: TileKind | 'cps', v: number | null): string {
  switch (kind) {
    case 'bps': return humanBps(v)
    case 'pps': return humanPps(v)
    case 'cps': return cell(v) === '–' ? '–' : `${cell(v)} cps`
    case 'cpu': return pct(v)
    default: return cell(v)
  }
}

function MonitorTile({ t }: { t: TileSpec }) {
  const cpu = t.kind === 'cpu' && t.value !== null
  return (
    <Tile name={t.name} text={formatKind(t.kind, t.value)} red={t.flagged} flag="drops" hint={t.hint}>
      {cpu && <meter min={0} max={100} value={Math.min(100, Math.max(0, t.value ?? 0))} aria-label={`${t.name} load`} />}
    </Tile>
  )
}

type Pick = (h: MonitorHistory) => (number | null)[]
const chart = (title: string, series: { label: string; color: string; pick: Pick }[], format: (v: number | null) => string) => (
  <TimeChart key={title} legend={series.length > 1 ? 'chips' : 'line'} title={title} subscribe={monitorStore.subscribe} windowS={CHART_WINDOW_S} format={format}
    series={series.map(({ label, color }) => ({ label, color }))}
    latestTs={() => monitorStore.history().ts.at(-1)}
    data={() => { const h = monitorStore.history(); return [h.ts, ...series.map((s) => s.pick(h))] }} />
)

// The Global panel: tiles grouped Tx, Rx, Flows and System; what the profile expects against what is measured; then the charts.
export function MonitorGlobal() {
  const g = useMonitor((m) => m?.global ?? null)
  const layout = useMemo(() => globalTiles(g), [g])
  return (
    <div className="monitor-global">
      <div className="tiles">
        {GROUPS.map((name) => (
          <TileGroup key={name} name={name}>
            {layout.tiles.filter((t) => t.group === name).map((t) => <MonitorTile key={t.id} t={t} />)}
          </TileGroup>
        ))}
      </div>
      <table className="streams" aria-label="Expected against measured">
        <thead><tr><th>Rate</th><th>Expected</th><th>Measured</th><th>Measured / expected</th></tr></thead>
        <tbody>
          {layout.expected.map((r) => (
            <tr key={r.id}><th scope="row">{r.name}</th><td>{formatKind(r.kind, r.expected)}</td><td>{formatKind(r.kind, r.measured)}</td><td>{pct(r.pct)}</td></tr>
          ))}
        </tbody>
      </table>
      <div className="monitor-charts">
        {chart('Monitor bits per second', [{ label: 'Tx bps', color: 'var(--series-1)', pick: (h) => h.tx_bps }, { label: 'Rx bps', color: 'var(--series-2)', pick: (h) => h.rx_bps }], humanBps)}
        {chart('Monitor packets per second', [{ label: 'Tx pps', color: 'var(--series-1)', pick: (h) => h.tx_pps }, { label: 'Rx pps', color: 'var(--series-2)', pick: (h) => h.rx_pps }], humanPps)}
        {chart('Monitor CPU', [{ label: 'CPU', color: 'var(--series-3)', pick: (h) => h.cpu }], pct)}
      </div>
    </div>
  )
}
