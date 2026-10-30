import type { PortView } from '../api'
import type { Snapshot } from '../stats'
import { pgidStore } from '../pgidStats'
import { DROP_RATE_HINT, DROP_RATE_NAME } from '../monitorLayouts'
import { Tile, TileGroup } from './Tile'
import { usePgidSelector, useStatsSelector } from '../useStats'
import { cell, humanBps, humanPps, l1Bps } from '../statsFormat'

const RED_AT = 85

const g = (key: string) => (s: Snapshot) => s.latest?.global[key] ?? null

function CpuTile({ name, k }: { name: string; k: string }) {
  const v = useStatsSelector(g(k))
  return <Tile name={name} text={cell(v) === '–' ? '–' : `${cell(v)} %`} red={v !== null && v >= RED_AT} />
}
function BpsTile({ name, k, hint }: { name: string; k: string; hint?: string }) {
  return <Tile name={name} text={humanBps(useStatsSelector(g(k)))} hint={hint} />
}
function L1Tile() {
  const v = useStatsSelector((s) => l1Bps(s.latest?.global.tx_bps ?? null, s.latest?.global.tx_pps ?? null))
  return <Tile name="Tx L1" text={humanBps(v)} />
}
function RawTile({ name, k, pps }: { name: string; k: string; pps?: boolean }) {
  const v = useStatsSelector(g(k))
  return <Tile name={name} text={pps ? humanPps(v) : cell(v)} />
}

// The distinct pg_ids of both types (inventory A8.1); a dash until the first pgid message.
function StreamsTile() {
  const n = usePgidSelector(() => (pgidStore.latest() === null ? null : pgidStore.ids().length))
  return <Tile name="Streams" text={n === null ? '–' : String(n)} />
}

export function GlobalTiles({ ports }: { ports: PortView[] }) {
  const active = ports.filter((p) => p.state === 'TX' || p.state === 'PCAP_TX').length
  return (
    <div className="tiles">
      <TileGroup name="Tx">
        <BpsTile name="Tx L2" k="tx_bps" />
        <L1Tile />
        <RawTile name="PPS" k="tx_pps" pps />
        <RawTile name="Queue full" k="queue_full" />
      </TileGroup>
      <TileGroup name="Rx">
        <BpsTile name="Rx L2" k="rx_bps" />
        <BpsTile name={DROP_RATE_NAME} k="rx_drop_bps" hint={DROP_RATE_HINT} />
      </TileGroup>
      <TileGroup name="System">
        <CpuTile name="CPU" k="cpu_util" />
        <CpuTile name="Rx CPU" k="rx_cpu_util" />
        <Tile name="Active ports" text={String(active)} />
        <StreamsTile />
      </TileGroup>
    </div>
  )
}
