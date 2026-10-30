import type { Neighbour, PingMsg, PortView, XstatsView } from './api'
import portViews from './__fixtures__/portViews.json'
import portViewsP3b from './__fixtures__/portViewsP3b.json'
import pingMsg from './__fixtures__/pingMsg.json'
import neighbours from './__fixtures__/neighbours.json'
import xstats from './__fixtures__/xstats.json'

// The profiles map the backend publishes for a port in `state` whose default profile `_` is the one in that state.
// The client never records IDLE (trex_stl_port.py:67-68) and backend/trex_web_gui/trexapi/ports.py _drop_idle_profiles
// removes it, so an IDLE port has no `_` entry at all. A DOWN port keeps the profiles it had (link_up false, state DOWN);
// this helper gives it none, so a test of a transmitting port that lost link sets `profiles` by hand.
export const wireProfiles = (state: string | undefined): PortView['profiles'] =>
  state && state !== 'IDLE' && state !== 'DOWN' ? { _: state } : {}

// Recorded port-0 PortViews, one per scenario, captured from what the backend publishes while the stateful fake
// plays that scenario (tests/contract/test_wire_fixtures.py; refresh with `make wire-fixtures`).
// `owner` is "<local-user>" for the session's own name. Prefer these over hand-written views.
export type WireScenario =
  | 'idle_unowned' | 'idle_owned' | 'streams_owned' | 'tx_owned' | 'paused_owned'
  | 'foreign_bench_tx' | 'foreign_bench_tx_unowned' | 'service_on' | 'service_filtered'

export const wirePort = (name: WireScenario, overrides: Partial<PortView> = {}): PortView =>
  ({ ...(portViews as Record<WireScenario, PortView>)[name], ...overrides })

// Port 0's PortView per layer-configuration scenario (tests/contract/test_wire_fixtures.py: capture_p3b). The IPv6 ones come
// from a fake started --stack linux_based, because the recorded legacy guest refuses to enable IPv6.
export type WireLayerScenario = 'l2' | 'l3_resolved' | 'l3_unresolved' | 'vlan_two_tags' | 'ipv6_auto' | 'ipv6_address'

export const wireLayerPort = (name: WireLayerScenario, overrides: Partial<PortView> = {}): PortView =>
  ({ ...(portViewsP3b as Record<WireLayerScenario, PortView>)[name], ...overrides })

// The `ping` messages the session publishes for Task 1's recorded echoes; ts is 0.
export type WirePingScenario = 'reply' | 'timeout' | 'reply_v6'
export const wirePing = (name: WirePingScenario, overrides: Partial<PingMsg> = {}): PingMsg =>
  ({ ...(pingMsg as Record<WirePingScenario, PingMsg>)[name], ...overrides })

// The body of POST /api/ports/scan6 for the recorded neighbour, and of GET /api/ports/0/xstats.
export const wireNeighbours = (): { neighbours: Neighbour[]; service_restore_error: string | null } =>
  (neighbours as { scan: { neighbours: Neighbour[]; service_restore_error: string | null } }).scan
export const wireXstats = (): XstatsView => (xstats as { port0: XstatsView }).port0
