import pgidMsg from './__fixtures__/pgidMsg.json'
import utilMsg from './__fixtures__/utilMsg.json'

// Recorded `pgid` and `util` messages, as the backend publishes them while the stateful fake plays flow_latency.py
// (tests/contract/test_wire_fixtures.py; refresh with `make wire-fixtures`). `ts` is 0; the fake's clock is stepped
// 10 s once, so counters are rate x 10 s and repeatable. Everything else is as published. What they cannot show: the
// fake has no ramp, loss or per-port Rx other than the pair (port 0 Tx is port 1 Rx), 'readded' has zero counters,
// and the latency block is the one recorded sample copied unchanged. Prefer these over hand-written messages.
export type PgidScenario = 'tx' | 'stopped' | 'readded'
export type UtilScenario = 'tx'

// A per-port map; its keys are the port numbers as strings plus "total".
export type PortMap = Record<string, number | null>

export interface WirePgid {
  type: 'pgid'
  ts: number
  interval_s: number
  active: { flow_stats: number[]; latency: number[] }
  truncated: boolean
  ver_id: Record<string, number>
  flow: Record<string, Record<string, PortMap>>
  // Absent for a latency id that has seen no traffic yet.
  latency: Record<string, {
    err_cntrs: Record<string, number | null>
    latency: { average: number | null; jitter: number | null; total_max: number | null; total_min: number | null
      last_max: number | null; histogram: Record<string, number> }
  }>
  flow_global: { rx_err: PortMap; tx_err: PortMap }
}

export interface WireUtil {
  type: 'util'
  ts: number
  // ports: -1 means no port (an idle slot)
  cpu: { ports: number[]; history: (number | null)[] }[]
  mbuf: Record<string, Record<string, { free: number | null; total: number | null }>>
}

export const wirePgid = (name: PgidScenario): WirePgid =>
  JSON.parse(JSON.stringify((pgidMsg as unknown as Record<PgidScenario, WirePgid>)[name])) as WirePgid

export const wireUtil = (name: UtilScenario): WireUtil =>
  JSON.parse(JSON.stringify((utilMsg as unknown as Record<UtilScenario, WireUtil>)[name])) as WireUtil
