import type { Point } from './stats'
import { humanBps, humanPps } from './statsFormat'

export type ChartSeries = { id: string; label: string; value(p: Point): number | null; format(v: number | null): string }

// The charts read the `total` scope (the client's per-port sum). Tx bps L2 is the tx value:
// the legacy GUI plotted Rx here (inventory A8.5, measured).
const field = (key: string) => (p: Point): number | null => {
  const v = p[key]
  return typeof v === 'number' && Number.isFinite(v) && v >= 0 ? v : null
}

export const SERIES: ChartSeries[] = [
  { id: 'tx_pps', label: 'Tx pps', value: field('tx_pps'), format: humanPps },
  { id: 'rx_pps', label: 'Rx pps', value: field('rx_pps'), format: humanPps },
  { id: 'tx_bps_l2', label: 'Tx bps L2', value: field('tx_bps'), format: humanBps },
  { id: 'rx_bps_l2', label: 'Rx bps L2', value: field('rx_bps'), format: humanBps },
  { id: 'tx_bps_l1', label: 'Tx bps L1', value: field('tx_bps_L1'), format: humanBps },
  { id: 'rx_bps_l1', label: 'Rx bps L1', value: field('rx_bps_L1'), format: humanBps },
]

export const INTERVALS = [60, 90, 120, 300] as const
export const LAYOUTS = [1, 2, 4] as const
export const DEFAULT_INTERVAL = 60
export const DEFAULT_LAYOUT = 2
export const DEFAULT_CHARTS = ['tx_pps', 'rx_pps', 'tx_bps_l2', 'rx_bps_l2']

export const seriesById = (id: string): ChartSeries => SERIES.find((s) => s.id === id) ?? SERIES[0]
