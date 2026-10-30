import type { MonitorGlobalStats } from './api'

// The Global panel's and the Ports panel's contents, read from the `trex-global` keys the server publishes
// (src/main_dpdk.cpp:2826-2900 in the pinned TRex). Pure: no store, no React.

export type TileGroupName = 'Tx' | 'Rx' | 'Flows' | 'System'
export type TileKind = 'bps' | 'pps' | 'cps' | 'count' | 'cpu'
export interface TileSpec { id: string; name: string; group: TileGroupName; kind: TileKind; value: number | null; flagged?: boolean; hint?: string }
// One row of the expected-versus-measured table. pct is null when nothing is expected (no division by zero).
export interface ExpectedRow { id: string; name: string; kind: 'bps' | 'pps' | 'cps'; expected: number | null; measured: number | null; pct: number | null }
export interface GlobalLayout { tiles: TileSpec[]; expected: ExpectedRow[]; dropFlagged: boolean }
export interface PortRow {
  port: number; opackets: number | null; ipackets: number | null; obytes: number | null; ibytes: number | null
  tx_bps: number | null; rx_bps: number | null; tx_pps: number | null; rx_pps: number | null; ierrors: number | null; oerrors: number | null
}

export const DROP_FLAG_BPS = 1   // C3: a drop rate above 1 bps is flagged
export const DROP_RATE_NAME = 'Drop rate (Tx − Rx)'
// TRex's m_rx_drop_bps is total Tx minus total Rx, zeroed below 10 % of Tx (trex-core src/main_dpdk.cpp:4906-4915), so
// a one-way test reads as all dropped (RC1-41).
export const DROP_RATE_HINT = "TRex's drop rate: the bits per second sent minus those received, over all ports (0 while "
  + 'the difference is under 10 % of Tx). Traffic that does not come back to a TRex port counts as dropped, so this is '
  + 'a loss figure only when the traffic loops back to TRex, through a device under test or a cable.'

type G = MonitorGlobalStats | null | undefined
const num = (g: G, key: string): number | null => {
  const v = g?.[key]
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

// m_total_nat_* counters worth a tile only once they count something.
const NAT: [string, string][] = [
  ['m_total_nat_open', 'NAT open'], ['m_total_nat_active', 'NAT active'], ['m_total_nat_syn_wait', 'NAT SYN wait'],
  ['m_total_nat_time_out', 'NAT timeout'], ['m_total_nat_time_out_wait_ack', 'NAT timeout (wait ACK)'],
  ['m_total_nat_no_fid', 'NAT no flow id'], ['m_total_nat_learn_error', 'NAT learn error'],
]

export function globalTiles(g: G): GlobalLayout {
  const n = (key: string) => num(g, key)
  const drop = n('m_rx_drop_bps')
  const dropFlagged = drop !== null && drop > DROP_FLAG_BPS
  const tile = (id: string, name: string, group: TileGroupName, kind: TileKind, key: string, extra: Partial<TileSpec> = {}): TileSpec =>
    ({ id, name, group, kind, value: n(key), ...extra })
  const tiles: TileSpec[] = [
    tile('tx_bps', 'Tx bps', 'Tx', 'bps', 'm_tx_bps'),
    tile('tx_pps', 'Tx pps', 'Tx', 'pps', 'm_tx_pps'),
    tile('tx_cps', 'Tx cps', 'Tx', 'cps', 'm_tx_cps'),
    tile('total_tx_pkts', 'Packets sent', 'Tx', 'count', 'm_total_tx_pkts'),
    tile('total_tx_bytes', 'Bytes sent', 'Tx', 'count', 'm_total_tx_bytes'),
    tile('rx_bps', 'Rx bps', 'Rx', 'bps', 'm_rx_bps'),
    tile('rx_pps', 'Rx pps', 'Rx', 'pps', 'm_rx_pps'),
    tile('rx_drop_bps', DROP_RATE_NAME, 'Rx', 'bps', 'm_rx_drop_bps', { flagged: dropFlagged, hint: DROP_RATE_HINT }),
    tile('total_rx_pkts', 'Packets received', 'Rx', 'count', 'm_total_rx_pkts'),
    tile('total_rx_bytes', 'Bytes received', 'Rx', 'count', 'm_total_rx_bytes'),
    tile('active_flows', 'Active flows', 'Flows', 'count', 'm_active_flows'),
    tile('open_flows', 'Open flows', 'Flows', 'count', 'm_open_flows'),
    tile('cpu_util', 'CPU', 'System', 'cpu', 'm_cpu_util'),
    tile('rx_cpu_util', 'Rx CPU', 'System', 'cpu', 'm_rx_cpu_util'),
    tile('alloc_error', 'Alloc errors', 'System', 'count', 'm_total_alloc_error'),
    tile('queue_full', 'Queue full', 'System', 'count', 'm_total_queue_full'),
    tile('queue_drop', 'Queue drop', 'System', 'count', 'm_total_queue_drop'),
  ]
  for (const [key, name] of NAT) {
    const v = n(key)
    if (v !== null && v !== 0) tiles.push({ id: key.replace(/^m_total_/, ''), name, group: 'Flows', kind: 'count', value: v })
  }
  const row = (id: string, name: string, kind: ExpectedRow['kind'], exp: string, got: string): ExpectedRow => {
    const expected = n(exp), measured = n(got)
    return { id, name, kind, expected, measured, pct: expected !== null && expected > 0 && measured !== null ? (measured / expected) * 100 : null }
  }
  return {
    tiles,
    expected: [
      row('bps', 'Bit rate', 'bps', 'm_tx_expected_bps', 'm_tx_bps'),
      row('pps', 'Packet rate', 'pps', 'm_tx_expected_pps', 'm_tx_pps'),
      row('cps', 'Connection rate', 'cps', 'm_tx_expected_cps', 'm_tx_cps'),
    ],
    dropFlagged,
  }
}

// One row per port N that has an `opackets-N` key, in port order.
export function portRows(g: G): PortRow[] {
  if (!g) return []
  const ports = Object.keys(g).flatMap((k) => { const m = /^opackets-(\d+)$/.exec(k); return m ? [Number(m[1])] : [] }).sort((a, b) => a - b)
  return ports.map((p) => ({
    port: p, opackets: num(g, `opackets-${p}`), ipackets: num(g, `ipackets-${p}`), obytes: num(g, `obytes-${p}`), ibytes: num(g, `ibytes-${p}`),
    tx_bps: num(g, `m_total_tx_bps-${p}`), rx_bps: num(g, `m_total_rx_bps-${p}`), tx_pps: num(g, `m_total_tx_pps-${p}`), rx_pps: num(g, `m_total_rx_pps-${p}`),
    ierrors: num(g, `ierrors-${p}`), oerrors: num(g, `oerrors-${p}`),
  }))
}

const numOf = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const field = (o: object | null | undefined, key: string): unknown => (o ? (o as Record<string, unknown>)[key] : undefined)

export interface TemplateRow { name: string; tx: number | null; rx: number | null; errors: number | null; jitter: number | null; flagged: boolean }

// The Templates panel's rows, one per template name, by index. `tx` is the i-th entry of `trex-global`'s `template` array, a plain
// number (the packets sent by that template; recorded as `"template": [0, 0, 2, 0, ...]`, 32 entries, in __fixtures__/monitorStateful.json;
// src/main_dpdk.cpp:2900 in the pinned TRex), present without rx-check. `errors`, `rx` and `jitter` are `val`, `rx_pkts` and `jitter` of
// the i-th `rx-check` `template` entry ({id, jitter, rx_pkts, val}; src/rx_check.cpp:444-463), null without rx-check.
// Flagged as the legacy GUI did (perportwin.cpp:132-138): tx > 0 and (errors > 100, or rx/tx < 0.7 with tx > 300). The rx/tx half needs rx.
export function templateRows(names: string[], global: object | null, rxCheck: object | null): TemplateRow[] {
  const txList = field(global, 'template')
  const rxList = field(rxCheck, 'template')
  return names.map((name, i) => {
    const tx = Array.isArray(txList) ? numOf(txList[i]) : null
    const entry: unknown = Array.isArray(rxList) ? rxList[i] : undefined
    const obj = typeof entry === 'object' && entry !== null ? entry : null
    const errors = numOf(field(obj, 'val')), rx = numOf(field(obj, 'rx_pkts')), jitter = numOf(field(obj, 'jitter'))
    const flagged = tx !== null && tx > 0 && ((errors !== null && errors > 100) || (rx !== null && tx > 300 && rx / tx < 0.7))
    return { name, tx, rx, errors, jitter, flagged }
  })
}

export interface StfLatencyRow {
  port: number; side: 'client' | 'server'
  avg: number | null; max: number | null; cmax: number | null; error: number | null; jitter: number | null
}

// One row per port with an `avg-N`, `max-N`, `c-max-N`, `error-N` or `jitter-N` key (src/stateful_rx_core.cpp:338-360 in the pinned
// TRex), in port order. Even ports are the client side, odd ports the server side.
export function stfLatencyRows(lat: Record<string, number> | null | undefined): StfLatencyRow[] {
  if (!lat) return []
  const ports = new Set<number>()
  for (const k of Object.keys(lat)) { const m = /^(?:avg|max|c-max|error|jitter)-(\d+)$/.exec(k); if (m) ports.add(Number(m[1])) }
  return [...ports].sort((a, b) => a - b).map((p) => ({
    port: p, side: p % 2 === 0 ? 'client' : 'server',
    avg: numOf(lat[`avg-${p}`]), max: numOf(lat[`max-${p}`]), cmax: numOf(lat[`c-max-${p}`]), error: numOf(lat[`error-${p}`]), jitter: numOf(lat[`jitter-${p}`]),
  }))
}

// The highest `c-max-N` over the client (even) ports and over the server (odd) ports; null for a side with none. `c-max-N` is TRex's
// maximum over the last period (time_histogram.h:112-119); `max-N` is the all-time maximum and never falls, so it cannot feed a 16 s window.
export function clientServerMax(lat: Record<string, number> | null | undefined): { client: number | null; server: number | null } {
  const best: Record<'client' | 'server', number | null> = { client: null, server: null }
  for (const r of stfLatencyRows(lat)) {
    if (r.cmax !== null && (best[r.side] === null || r.cmax > best[r.side]!)) best[r.side] = r.cmax
  }
  return best
}
