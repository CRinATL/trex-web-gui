export type SessionState = 'disconnected' | 'connecting' | 'connected' | 'lost'
export interface ConnectParams { host: string; sync_port: number; async_port: number; timeout_s: number; read_only?: boolean }
export interface ServerInfo { version: string; mode: string; build_date: string; hostname: string; port_count: number; core_type: string; ports: Record<string, unknown>[] }
// What the backend resolved for one port (backend hostinfo.PortIface). kernel_name is set only when confirmed against the port.
export interface PortIface { port: number; cfg_entry: string | null; kind: 'af_packet' | 'pci' | 'dummy' | 'vdev' | null; kernel_name: string | null
  pci_addr: string | null; driver: string | null; vendor_device: string | null; model: string | null; confirmed: boolean; note: string | null }
export interface SessionSnapshot { type: 'session'; state: SessionState; params: ConnectParams | null; info: ServerInfo | null; error: string | null; rpc_console: boolean; include_polling: boolean
  // "<ns>:<n>", changes on every connect attempt and never repeats across a GUI restart (backend trex_session.py snapshot)
  // optional only so the many hand-built snapshots in older tests still type-check; the backend always sends it
  connect_seq?: string
  // optional for the same reason; the backend always sends both
  ifaces?: PortIface[]; ifaces_note?: string | null }
export interface ClientEventMsg { type: 'event'; name: string; args: unknown[]; message: string | null; ts: number; level: string }
// monitor_only and monitor_target are optional only so the many hand-built hellos in older tests still type-check; the backend always sends both.
export interface HelloMsg { type: 'hello'; app_version: string; server_hint: string | null; server_ports: { sync_port: number; async_port: number } | null
  monitor_only?: boolean; monitor_target?: MonitorTarget | null
  // The Daemon dialog's target for the --server host (backend app.py hello); optional for the same reason.
  daemon_hint?: DaemonHint | null }
// The client's cached layer_cfg (attr_to_json; recorded in __fixtures__/portViewsP3b.json). ether.dst is the last known
// destination: it is valid only while ether.state is "configured". ipv4 carries src/dst only outside state "none". ipv6.src is ""
// for "auto" on the linux_based stack. All fields are optional only so a hand-built view with `{}` still type-checks; a published view has them.
export interface LayerCfg {
  ether?: { src: string; dst: string; state: 'configured' | 'unconfigured' }
  ipv4?: { state: 'none' | 'unresolved' | 'resolved'; src?: string; dst?: string }
  ipv6?: { enabled: boolean; src?: string | null }
}
export interface PortView {
  id: number; state: string; owner: string; owned: boolean; service: boolean; service_filtered: boolean
  // owned under this session's user name by another session (backend trexapi/ports.py held_elsewhere_by_me); optional only
  // so the many hand-built views in tests still type-check, as connect_seq is; the backend always sends it
  mine_elsewhere?: boolean
  link_up: boolean | null; speed_gbps: number | null; promiscuous: boolean | null; multicast: boolean | null
  fc_mode: number | null; vlan: number[]; layer_cfg: LayerCfg; rx_filter_mode: string | null
  profiles: Record<string, string>; rx_info: Record<string, unknown>; info: Record<string, unknown>
}
export interface PortsMsg { type: 'ports'; ports: PortView[] }
export interface LogMsg { type: 'log'; level: 'info' | 'warning' | 'error' | 'ping'; message: string; ts: number }
export interface RpcMsg { type: 'rpc'; direction: 'request' | 'response'; body: unknown; truncated?: number; ts: number; thread: string }
export interface StatsMsg { type: 'stats'; ts: number; stats_epoch?: number; interval_s: number; global: Record<string, number | null>; ports: Record<string, Record<string, number | null>>; total: Record<string, number | null> }
// Mirrors backend PgidView / UtilView / port_labels (recorded in __fixtures__/pgidMsg.json, utilMsg.json). A per-port map has the port numbers as keys plus "total".
export type PortMap = Record<string, number | null>
export interface PgidMsg {
  type: 'pgid'; ts: number; interval_s: number
  active: { flow_stats: number[]; latency: number[] }
  truncated: boolean
  ver_id: Record<string, number>
  flow: Record<string, Record<string, PortMap>>
  // Absent for an active latency id that has seen no traffic yet.
  latency: Record<string, {
    err_cntrs: Record<string, number | null>
    latency: { average: number | null; jitter: number | null; total_max: number | null; total_min: number | null; last_max: number | null; histogram: Record<string, number> }
  }>
  flow_global: { rx_err: PortMap; tx_err: PortMap }
}
export interface UtilMsg {
  type: 'util'; ts: number
  cpu: { ports: number[]; history: (number | null)[] }[]
  mbuf: Record<string, Record<string, { free: number | null; total: number | null }>>
}
// label = { name, description }, keyed by server then label key.
export interface PortLabelsMsg { type: 'port_labels'; labels: Record<string, Record<string, { name: string; description: string }>>; load_error: string | null }
// One echo of a ping, published as it happens (trex_session.py ping()). "refused" and "error" are the final line of a ping that ended early.
export type PingStatus = 'reply' | 'timeout' | 'unreachable' | 'refused' | 'error'
export interface PingLine { status: PingStatus; text: string; rtt_ms: number | null }
// run: session-wide id of one ping call, also on the POST response, so a line that arrives after its response can be told apart.
export interface PingMsg extends PingLine { type: 'ping'; port: number; run: number; seq: number; count: number; ts: number }
// What holds a port's executor: a ping, a scan, or an Apply. Published whole: the ports that are busy now, {} when none.
export type BusyKind = 'ping' | 'scan' | 'apply'
export interface BusyMsg { type: 'busy'; ports: Record<number, BusyKind> }
export type ServerMsg = SessionSnapshot | ClientEventMsg | HelloMsg | PortsMsg | LogMsg | RpcMsg | PortLabelsMsg | PingMsg | BusyMsg | CaptureMsg | CaptureSaveMsg | SetupsMsg | SettingsMsg | MonitorMsg | DaemonMsg
export interface Tunable { dest: string; flag: string; kind: 'int' | 'float' | 'str' | 'bool_true' | 'bool_false' | 'choice' | 'raw'; default: unknown; choices: unknown[] | null; help: string }
export interface StreamSummary { index: number; name: string | null; enabled: boolean; self_start: boolean; mode: string; rate: string; packet: string; length: number; next: string | null; has_vm: boolean }
export interface Inspected { path: string; kind: 'py' | 'yaml' | 'json'; tunables: Tunable[]; summary: StreamSummary[]; stream_count: number }
export interface FsEntry { name: string; dir: boolean; size: number | null }
export interface FsList { path: string; parent: string | null; entries: FsEntry[]; truncated: boolean }
export interface SavedConnection extends ConnectParams { name: string }
export interface Preferences { templates_dir: string; wireshark_path: string; workspace_dir: string; release_on_exit: boolean; trex_cfg_path: string
  // the profile library directory; optional only so hand-built test objects still type-check, the backend always sends it
  library_dir?: string
  // the largest capture the packet editor's picker opens, in MiB (1-1024); optional for the same reason
  pcap_size_cap_mib?: number }
export interface Settings { preferences: Preferences; connections: SavedConnection[] }
export interface About { app_version: string; trex_pin: { upstream_sha: string; patches: string[] }; supported: string; host: string }

export class ApiError extends Error {
  status: number
  code: string
  // the whole error body, for the fields beyond error and message (a bundle apply's storage_error carries written and failed)
  body: Record<string, unknown>
  // no parameter properties: the Vite template's tsconfig sets erasableSyntaxOnly
  constructor(status: number, code: string, message: string, body: Record<string, unknown> = {}) { super(message); this.status = status; this.code = code; this.body = body }
}

// Dispatched on window for every 401: the cookie is missing or belongs to another launch of the backend.
export const UNAUTHENTICATED_EVENT = 'trex-web-gui:unauthenticated'

// Raises ApiError for a failed reply (and the 401 event), else returns the parsed body.
async function reply<T>(r: Response): Promise<T> {
  const j = await r.json().catch(() => ({}))
  if (r.status === 401) window.dispatchEvent(new Event(UNAUTHENTICATED_EVENT))
  if (!r.ok) {
    const msg = typeof j.message === 'string' ? j.message : Array.isArray(j.detail) ? j.detail.map((d: { msg: string }) => d.msg).join('; ') : r.statusText
    throw new ApiError(r.status, j.error ?? 'http_error', msg, j)
  }
  return j as T
}

// A request that got no HTTP reply at all (the backend stopped, the SSH tunnel dropped) rejects with the browser's own
// TypeError, whose text names neither the request nor a cause: Safari's is "Load failed", Chrome's "Failed to fetch"
// (RC1-27). It becomes ApiError status 0, code no_reply, saying which request and what to check.
export const noReplyText = (method: string, path: string, browser: string) =>
  `No reply from trex-web-gui to ${method} ${path} (the browser says "${browser}"). Is trex-web-gui serve still running, and the SSH tunnel up?`

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  let r: Response
  try {
    r = await fetch(path, { method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined })
  } catch (x) {
    if (x instanceof TypeError) throw new ApiError(0, 'no_reply', noReplyText(method, path, x.message))
    throw x
  }
  return reply<T>(r)
}

// Exempt from the token gate: it answers whether this browser's cookie is accepted.
export const authStatus = async (): Promise<boolean> => (await call<{ authenticated: boolean }>('GET', '/api/auth/status')).authenticated
export const getAbout = () => call<About>('GET', '/api/about')
// GET and PUT /api/settings answer this; the `settings` WebSocket message is the same body with a type. data_dir is where the GUI keeps its files.
export interface SettingsReply { settings: Settings; load_error: string | null; data_dir?: string }
export interface SettingsMsg { type: 'settings'; settings: Settings; load_error: string | null; data_dir: string }
export const getSettings = () => call<SettingsReply>('GET', '/api/settings')
export const putSettings = (s: Settings) => call<SettingsReply>('PUT', '/api/settings', s)

// The settings bundle (backend/trex_web_gui/store/bundle.py). `sections` is read by the backend's own parsers, so it stays opaque here;
// `theme` is added by the browser on export and is advisory on import.
export interface Bundle { format: string; version: number; exported_at?: string; app_version?: string; sections: Record<string, unknown> }
export type BundleMode = 'replace' | 'merge' | 'skip'
export interface BundleSectionPreview { present: boolean; valid: boolean; errors: string[]; adds: string[]; changes: string[]; removes: string[]; flags: string[] }
export interface PathChange { slug: string; port: number; from: string; to: string }
export interface BundlePreview { version: number; sections: Record<string, BundleSectionPreview>; ignored: string[]; profile_paths: string[]; path_changes: PathChange[] }
export interface BundleResult { written: string[]; skipped: string[] }
export const exportBundle = () => call<Bundle>('GET', '/api/settings/bundle')
export const previewBundle = (b: Bundle) => call<BundlePreview>('POST', '/api/settings/bundle/preview', b)
// modes: section name -> mode. A 500 storage_error carries `written` and `failed` in ApiError.body (section names, or test_setups/<slug>).
export const applyBundle = (bundle: Bundle, modes: Record<string, BundleMode>) => call<BundleResult>('POST', '/api/settings/bundle/apply', { bundle, modes })
export const getSession = () => call<SessionSnapshot>('GET', '/api/session')
export const connect = (p: ConnectParams) => call<{ info: ServerInfo }>('POST', '/api/session/connect', p)
export const disconnect = () => call<SessionSnapshot>('POST', '/api/session/disconnect')

type P = { ports: number[] }
export const getPorts = () => call<{ ports: PortView[] }>('GET', '/api/ports')
export const acquire = (b: P & { force?: boolean }) => call<{ ports: PortView[] }>('POST', '/api/ports/acquire', b)
export const release = (b: P) => call<{ ports: PortView[] }>('POST', '/api/ports/release', b)
export const reacquire = () => call<{ reacquired: number[]; ports: PortView[] }>('POST', '/api/ports/reacquire')
// Filtered service mode masks. Mirrors backend/trex_web_gui/_vendor/trex/common/trex_client.py:49-55 (canon:CMT-8).
export const SERVICE_MASK = { NO_TCP_UDP: 1, BGP: 2, DHCP: 4, TRANSPORT: 8, MDNS: 16, ALL: 255 } as const
// filtered needs enabled=false; mask (1..255) is allowed only with filtered and defaults to NO_TCP_UDP server-side.
export const setService = (b: P & { enabled: boolean; filtered?: boolean; mask?: number }) => call<{ ports: PortView[] }>('POST', '/api/ports/service', b)
export const setAttr = (b: P & Partial<{ promiscuous: boolean; multicast: boolean; link_up: boolean; led_on: boolean; flow_ctrl: number }>) => call<{ ports: PortView[] }>('POST', '/api/ports/attr', b)
export const resetPorts = (b: P) => call<{ ports: PortView[] }>('POST', '/api/ports/reset', b)
export const inspect = (b: { path: string; ports?: number[]; tunables?: string[] }) => call<Inspected>('POST', '/api/profiles/inspect', b)
export const assign = (b: P & { path: string; tunables: string[]; stop_first?: boolean }) =>
  call<{ summary: StreamSummary[]; stream_count: number; rate: Record<string, Record<string, unknown>>; ports: PortView[] }>('POST', '/api/ports/assign', b)
// This connection's last Assign to a port (backend TrexSession.assigned); Start sends what is on the port, not a path (RC1-30).
export interface AssignedRecord { path: string; name: string; tunables: string[]; summary: StreamSummary[]; stream_count: number }
export const assignedOn = (port: number) => call<{ streams: boolean; assigned: AssignedRecord | null }>('GET', `/api/ports/${port}/assigned`)
export const start = (b: P & { mult: string; duration?: number; force?: boolean }) => call<{ ports: PortView[] }>('POST', '/api/ports/start', b)
export const update = (b: P & { mult: string }) => call<{ ports: PortView[] }>('POST', '/api/ports/update', b)
export const pause = (b: P) => call<{ ports: PortView[] }>('POST', '/api/ports/pause', b)
export const resume = (b: P) => call<{ ports: PortView[] }>('POST', '/api/ports/resume', b)
export const stop = (b: P) => call<{ ports: PortView[] }>('POST', '/api/ports/stop', b)
export const rate = (port: number) => call<{ rate: Record<string, unknown> }>('GET', `/api/ports/${port}/rate`)
// includePolling omitted keeps the server's current value
export const setConsole = (enabled: boolean, includePolling?: boolean) =>
  call<{ enabled: boolean; include_polling: boolean }>('POST', '/api/console', { enabled, include_polling: includePolling })
// opts.pcap also lists .pcap and .cap files (the backend's include_pcap), for the Studio's pcap import.
export const fsList = (path?: string, opts?: { pcap?: boolean }) => {
  const q = [path === undefined ? '' : `path=${encodeURIComponent(path)}`, opts?.pcap ? 'include_pcap=true' : ''].filter(Boolean).join('&')
  return call<FsList>('GET', `/api/fs/list${q ? `?${q}` : ''}`)
}
export type PortLabelsReply = Pick<PortLabelsMsg, 'labels' | 'load_error'>
// The WebSocket delivers the labels on open and on every change, so the UI does not call this today.
export const getPortLabels = () => call<PortLabelsReply>('GET', '/api/port-labels')
// An empty name and description deletes the label. A 422 carries the reason in the ApiError message.
export const putPortLabel = (b: { server: string; key: string; name: string; description: string }) => call<PortLabelsReply>('PUT', '/api/port-labels', b)

// P3b port configuration. Bodies mirror backend/trex_web_gui/api/port_config.py LayerBody, PingBody and ScanBody.
export interface LayerBody {
  port: number; mode: 'L2' | 'L3'; vlan: number[]
  dst_mac?: string; src_ipv4?: string; dst_ipv4?: string
  // omitted leaves IPv6 alone; src omitted or null with enabled means auto
  ipv6?: { enabled: boolean; src?: string | null }
}
// The ARP try after an L3 Apply; "unresolved" is a warning, not a failed Apply (Apply itself succeeded).
export interface ArpResult { state: 'resolved' | 'unresolved'; mac: string | null; error: string | null }
// Non-null when turning service mode back off was refused: the command's result stands, the port is left in service mode.
export interface Restore { service_restore_error: string | null }
export interface Neighbour { type: string; mac: string; ipv6: string }
export interface XstatsView { port: number; values: Record<string, number> }
export const applyLayer = (b: LayerBody) => call<{ ports: PortView[]; arp: ArpResult | null } & Restore>('POST', '/api/ports/layer', b)
// Takes up to count x (1 s interval + 3 s timeout); the lines also arrive one by one as `ping` messages.
export const ping = (b: { port: number; dst: string; count?: number; pkt_size?: number }) => call<{ run: number; lines: PingLine[] } & Restore>('POST', '/api/ports/ping', b)
export const scan6 = (b: { port: number }) => call<{ neighbours: Neighbour[] } & Restore>('POST', '/api/ports/scan6', b)
export const getXstats = (port: number) => call<XstatsView>('GET', `/api/ports/${port}/xstats`)

// P3c capture. Every shape below is what the backend publishes; src/__fixtures__/capture*.json hold recorded copies
// (tests/contract/test_wire_fixtures.py: capture_p3c).
// One row of the server's capture list. `mine`: this GUI started it; `service`: its Rx port is held by a ping, ARP or scan.
// `snaplen` and `endpoint` are set for an endpoint capture only; `fetched` counts packets the server has handed out.
export interface CaptureRow {
  id: number; state: string; mode: string | null; tx: number[]; rx: number[]; bpf: string
  count: number; matched: number; fetched: number; limit: number; bytes: number
  endpoint: string | null; snaplen?: number | null; mine: boolean; service: boolean
}
// matched, fetched and count come from the monitor's own status row on every poll; matched - fetched - count is what the
// server dropped. `gone` is set once when the server no longer lists the capture.
export interface MonitorState {
  id: number; start_ts: number; tx: number[]; rx: number[]; bpf: string; limit: number
  fetched: number; matched: number; count: number; state: string; gone: string | null
}
export interface WiresharkState { running: boolean; error: string | null }
export interface CaptureMsg { type: 'capture'; ts: number; monitor: MonitorState | null; captures: CaptureRow[]; wireshark: WiresharkState }
export interface DecodedRow {
  index: number; ts_rel_us: number; port: number; origin: string; src: string; dst: string; proto: string
  wirelen: number; info: string; binary: string
}
export interface CapturePktsMsg { type: 'capture_pkts'; monitor_id: number; rows: DecodedRow[] }
// `file` is set when the Save finished; `error` when it stopped (the partial file is kept); `note` is optional.
export interface CaptureSaveMsg { type: 'capture_save'; id: number; fetched: number; total: number; file: SpoolFile | null; error: string | null; note?: string }
export interface SpoolFile {
  name: string; size: number; mtime: number; capture_id: number | null; server: string | null
  start_ts: number | null; pkt_count: number | null; partial: boolean
}
export type SpoolListing = { dir: string; files: SpoolFile[]; total: number }
// `ignored`: the field is not in the packet as built (a conditional field that is absent); the service sets it only when true.
export interface PacketField { id: string; value: string | number | { vtype: string; expr?: string; base64?: string }; hvalue: string | number; offset: number; length: number; ignored?: boolean }
export interface PacketLayer { id: string; offset: number; real_id: string; valid_structure: boolean; fields: PacketField[] }
export type PacketTree = { binary: string; data: PacketLayer[] }
export interface CaptureBody { tx: number[]; rx: number[]; bpf: string; limit: number }
export const getCapture = () => call<CaptureMsg>('GET', '/api/capture')
export const monitorStart = (b: CaptureBody) => call<{ monitor: MonitorState }>('POST', '/api/capture/monitor', b)
export const monitorStop = () => call<Record<string, never>>('DELETE', '/api/capture/monitor')
export const recorderStart = (b: CaptureBody) => call<{ id: number }>('POST', '/api/capture/recorders', b)
export const recorderStop = (id: number) => call<{ pkt_count: number }>('POST', `/api/capture/recorders/${id}/stop`)
export const recorderSave = (id: number) => call<{ file: SpoolFile }>('POST', `/api/capture/recorders/${id}/save`)
export const recorderRemove = (id: number) => call<Record<string, never>>('DELETE', `/api/capture/recorders/${id}`)
export const packetTree = (binary: string) => call<PacketTree>('POST', '/api/capture/packet/tree', { binary })
export const captureFiles = () => call<SpoolListing>('GET', '/api/capture/files')
export const captureFileUrl = (name: string) => `/api/capture/files/${encodeURIComponent(name)}`
export const deleteCaptureFile = (name: string) => call<Record<string, never>>('DELETE', captureFileUrl(name))
export const wiresharkStart = () => call<CaptureMsg>('POST', '/api/capture/wireshark')
export const wiresharkStop = () => call<CaptureMsg>('DELETE', '/api/capture/wireshark')
// Whether the backend host can launch Wireshark (the binary at the saved path, a display); `reason` says why not.
export const wiresharkPreflight = () => call<{ ok: boolean; reason: string | null }>('GET', '/api/capture/wireshark/preflight')
// P3c Task 11: the LLDP neighbours the current monitor has seen (backend trexapi/lldp.py LldpNeighbour), and the
// firmware caveat. A neighbour is dropped once its ttl runs out (expires = last_seen + ttl, backend clock).
export interface LldpNeighbour {
  port: number; vlan: number | null; src: string; chassis_id: string; chassis_id_subtype: string
  port_id: string; port_id_subtype: string; ttl: number; system_name: string | null; system_description: string | null
  port_description: string | null; management_addresses: string[]; capabilities: string[]; last_seen: number; expires: number
}
export interface LldpReply { neighbours: LldpNeighbour[]; note: string }
export const getLldp = () => call<LldpReply>('GET', '/api/capture/lldp')

// P3d saved test setups (backend/trex_web_gui/store/test_setups.py TestSetup and SetupPort). `layer` is LayerBody's normalised
// dump without `port`; `duration` is seconds, -1 for no limit; `created` and `updated` are set by the server.
export interface SetupLayer {
  mode: 'L2' | 'L3'; dst_mac: string | null; src_ipv4: string | null; dst_ipv4: string | null; vlan: number[]
  ipv6: { enabled: boolean; src: string | null } | null
}
export interface SetupPort {
  key: string; port: number; profile: { path: string; kind: Inspected['kind'] }
  tunables: { values: Record<string, unknown>; extra: string }
  mult: string; duration: number; force: boolean; acquire_force: boolean; stop_first: boolean; layer: SetupLayer | null
}
export interface SetupDashboard { tab: 'overview' | 'streams' | 'latency' | 'utilization'; streams_pgids: number[]; latency_pgids: number[]; window_s: number; layout: number }
export interface TestSetup {
  name: string; slug: string; created: string; updated: string; server: string | null; ports: SetupPort[]; dashboard: SetupDashboard | null
}
export interface SetupLoadError { file: string; error: string }
export interface SetupList { setups: TestSetup[]; load_errors: SetupLoadError[] }
export interface SetupsMsg extends SetupList { type: 'test_setups' }
// The WebSocket delivers the list on open and after every write. The PUT path must be the slug of the name (setups.ts slugify).
const setupPath = (slug: string) => `/api/test-setups/${encodeURIComponent(slug)}`
export const listSetups = () => call<SetupList>('GET', '/api/test-setups')
export const getSetup = (slug: string) => call<TestSetup>('GET', setupPath(slug))
export const putSetup = (slug: string, setup: TestSetup) => call<TestSetup>('PUT', setupPath(slug), setup)
export const renameSetup = (slug: string, name: string) => call<TestSetup>('POST', `${setupPath(slug)}/rename`, { name })
export const duplicateSetup = (slug: string, name: string) => call<TestSetup>('POST', `${setupPath(slug)}/duplicate`, { name })
export const deleteSetup = (slug: string) => call<SetupList>('DELETE', setupPath(slug))

// P4a Studio (backend/trex_web_gui/api/studio.py; recorded copies in src/__fixtures__/studio*.json).
// Errors are {error, message}; a 409 `stale` carries `rev` (the profile's current one) and a 422 `invalid_stream` carries `index` and `field`, both in ApiError.body.
export interface Source {
  kind: 'py' | 'yaml' | 'json' | 'pcap' | 'new'; path: string | null; args: string[]
  tunables: { values: Record<string, unknown>; extra: string } | null; ports_differ: boolean; imported: string
}
// `load_error` is set when the sidecar or JSON could not be read; `source_missing` when a .py source's file is gone.
export interface LibraryEntry {
  slug: string; name: string; path: string; stream_count: number; created: string; updated: string
  source: Source | null; source_missing: boolean; load_error: string | null
}
export interface LibraryList { profiles: LibraryEntry[]; load_errors: { file: string; error: string }[]; dir: string }
// The sidecar document (backend studio/sidecar.py); `streams` is parallel to the profile's streams, one object of GUI state each.
export interface Sidecar {
  version: number; name: string; created: string; updated: string; source: Source | null
  streams: Record<string, unknown>[]; pcap_wizard: Record<string, unknown> | null; stream_count: number
}
export interface ProfileDetail { entry: LibraryEntry; rev: string; sidecar: Sidecar }
// A stream as STLStream.to_json() writes it (backend studio/streams.py Stream). `name` and `next` are a string or, from a pcap import, an integer.
export interface StreamRate { type: 'pps' | 'bps_L1' | 'bps_L2' | 'percentage'; value: number }
export interface StreamMode { type: 'continuous' | 'single_burst' | 'multi_burst'; rate: StreamRate; total_pkts?: number; pkts_per_burst?: number; ibg?: number; count?: number }
export interface StreamFlowStats { enabled: boolean; rule_type?: 'stats' | 'latency' | 'tpg'; stream_id?: number; vxlan?: boolean; ieee_1588?: boolean }
export interface StreamJson {
  name?: string | number; next?: string | number; enabled: boolean; self_start: boolean; start_paused?: boolean; isg: number
  action_count: number; core_id?: number; flags: number; random_seed?: number; mode: StreamMode
  packet: { binary: string; meta?: string }; vm: { instructions: Record<string, unknown>[]; cache?: number; split_by_var?: string }
  flow_stats: StreamFlowStats
}
// A field-level patch: a key present replaces that field, null removes name, next or random_seed.
export type StreamPatch = Partial<Omit<StreamJson, 'name' | 'next' | 'random_seed'>> & { name?: string | number | null; next?: string | number | null; random_seed?: number | null }
// `length` is null (summary.proto "?") for a stored stream without a usable packet.
// `has_editor`: the stream's sidecar entry holds an editor document (served per row by streamSidecar, not in the page).
export interface StreamRow { index: number; stream: StreamJson; length: number | null; summary: { proto: string; info: string }; has_editor: boolean }
// An enabled stream whose next names a disabled one: saved, but the server refuses the profile at assign.
export interface NextWarning { index: number; name: string | number | null; next: string | number }
export interface StreamPage { rev: string; stream_count: number; names: (string | number | null)[]; offset: number; rows: StreamRow[]; warnings: NextWarning[] }
// A sidecar entry (backend studio/streams.py check_sidecar_entry): `{}` or `{ editor: { doc, binary, vm } }`.
export type SidecarEntry = Record<string, unknown>
// The most one sidecar entry may hold (backend SIDECAR_ENTRY_MAX), measured as the backend does.
export const SIDECAR_ENTRY_MAX = 256 * 1024
// `changed` is keyed by the old index as text; `order` lists old indices (or the text of one) in the new order.
// `sidecar` replaces the entries of those old indices; `added_sidecar` is parallel to `added`.
export interface StreamsDiff {
  rev: string; changed?: Record<string, StreamPatch>; added?: StreamJson[]; removed?: number[]; order?: (number | string)[] | null
  sidecar?: Record<string, SidecarEntry>; added_sidecar?: SidecarEntry[]
}
export interface DiffReply { rev: string; stream_count: number; renamed: Record<string, string>; cleared: string[]; warnings: NextWarning[] }
export interface SimStream {
  index: number; name: string | number | null; length: number; mode: string; rate_type: string; rate_value: number
  pps: number | null; bps_l1: number | null; bps_l2: number | null; percent: number | null; pkts: number | null; burst_s: number | null; enabled: boolean
}
export interface SimChain { start: string; steps: string[]; end: 'stop' | 'loop'; loop_to: string | null; action_count: number; pkts: number | null }
export interface SimReport {
  round_trip: { equal: boolean; differences: string[] }; streams: SimStream[]
  totals: { pps: number; bps_l1: number; bps_l2: number; percent: number; burst_pkts: number }
  chains: SimChain[]; unreachable: string[]; notes: string[]
}
export interface AddressRange { start: string; count: number }
export interface PcapOptions { ipg_usec: number | null; speedup: number; loop_count: number; name_prefix: string; rewrite_src: AddressRange | null; rewrite_dst: AddressRange | null }
// Exactly one of path (on the backend host) and upload_id. A pcap import needs `pcap`; tunables apply to a .py only.
export interface ImportBody {
  path?: string; upload_id?: string; kind?: 'py' | 'yaml' | 'json' | 'pcap'; tunables?: string[]
  tunable_form?: { values: Record<string, unknown>; extra: string }; pcap?: PcapOptions; name?: string; append_to?: { slug: string; rev: string }
}
export interface UploadReply { upload_id: string; name: string; size: number }
const studioPath = (slug: string) => `/api/studio/profiles/${encodeURIComponent(slug)}`
export const listLibrary = () => call<LibraryList>('GET', '/api/studio/profiles')
export const createProfile = (name: string) => call<LibraryEntry>('POST', '/api/studio/profiles', { name })
export const getProfile = (slug: string) => call<ProfileDetail>('GET', studioPath(slug))
// The reply is the listing after the delete.
export const deleteProfile = (slug: string) => call<LibraryList>('DELETE', studioPath(slug))
export const renameProfile = (slug: string, name: string, rev: string) => call<LibraryEntry>('POST', `${studioPath(slug)}/rename`, { name, rev })
export const duplicateProfile = (slug: string, name: string) => call<LibraryEntry>('POST', `${studioPath(slug)}/duplicate`, { name })
export const getStreams = (slug: string, offset: number, limit?: number) =>
  call<StreamPage>('GET', `${studioPath(slug)}/streams?offset=${offset}${limit === undefined ? '' : `&limit=${limit}`}`)
export const putStreams = (slug: string, diff: StreamsDiff) => call<DiffReply>('PUT', `${studioPath(slug)}/streams`, diff)
// One stream's sidecar entry; 404 for an index out of range.
export const streamSidecar = (slug: string, index: number) =>
  call<{ rev: string; entry: SidecarEntry }>('GET', `${studioPath(slug)}/streams/${index}/sidecar`)
// The .py a profile was imported from: 409 no_source, 404 source_missing, 422 bad_request, 413 too_large.
export const profileSource = (slug: string) => call<{ path: string; text: string }>('GET', `${studioPath(slug)}/source`)
export const getTemplate = () => call<StreamJson>('GET', '/api/studio/template')
// A raw PUT of the file's bytes (not multipart); the backend refuses a suffix outside .yaml .yml .json .pcap .cap and a body over 64 MiB.
export async function uploadFile(file: File): Promise<UploadReply> {
  return reply<UploadReply>(await fetch(`/api/studio/uploads?name=${encodeURIComponent(file.name)}`, { method: 'PUT', headers: { 'Content-Type': 'application/octet-stream' }, body: file }))
}
// The reply is the new entry, or a DiffReply when append_to is given.
export const importProfile = (body: ImportBody) => call<LibraryEntry | DiffReply>('POST', '/api/studio/import', body)
export const exportUrl = (slug: string, format: 'json' | 'yaml' | 'py', stream?: number) =>
  `${studioPath(slug)}/export?format=${format}${stream === undefined ? '' : `&stream=${stream}`}`
export const simulate = (slug: string) => call<SimReport>('POST', `${studioPath(slug)}/simulate`)

// P4b packet editor (backend api/packet.py; recorded copies in src/__fixtures__/packet/*.json). Errors are {error, message}:
// 422 `invalid_doc` carries `where` (`layer 1`, `instruction 0` or a key path), 422 `invalid_stream` carries `field`, and 503
// `engine_restarted` means the packet worker was killed while evaluating this request (the next request starts a new one).
export type VtypeValue = {
  vtype: 'EXPRESSION' | 'BYTES' | 'RANDOM' | 'UNDEFINED' | 'MACHINE' | 'OBJECT'
  expr?: string; base64?: string; value?: unknown
  generate?: 'random_bytes' | 'random_ascii' | 'template' | 'template_code'; template?: string; template_code?: string
  template_base64?: string; size?: number; total_size?: number; seed?: number
}
// A field value in a document: text, a number, a vtype object, or a list of those (null is an unset value).
export type FieldValue = string | number | boolean | null | VtypeValue | FieldValue[]
export interface DocField { id: string; value: FieldValue }
export interface DocLayer { id: string; fields: DocField[] }
// Field Engine parameter values are strings or vtype objects (the service lowercases a string).
export type FeValue = string | VtypeValue
export interface FeInstruction { id: string; parameters: Record<string, FeValue> }
// The `.trp` document; `fePrarameters` is the file format's own spelling.
export interface PacketDoc {
  fileType: string; version: string; metadata: { caption: string }; packet: DocLayer[]
  fePrarameters: Record<string, FeValue>; feInstructions: FeInstruction[]
}
export interface BitValue { name: string; value: number }
export interface BitDef { mask: number; name: string; values: BitValue[] }
// A field the service types is one of these; the 1873 of 2000-odd fields it does not type are edited as text.
export type FieldKind = 'BYTES' | 'MAC_ADDRESS' | 'ENUM' | 'NUMBER' | 'BITMASK' | 'STRING' | 'IP_ADDRESS' | 'IP_OPTIONS' | 'TCP_OPTIONS'
export interface FieldDef {
  id: string; name: string; type?: FieldKind; regex?: string; values_dict?: Record<string, number>
  auto?: boolean; bits?: BitDef[]; min?: number; max?: number
}
export interface ProtocolDef { id: string; name: string; fields: FieldDef[] }
// `help` is base64 of the instruction's docstring; `parameters` lists its parameter ids in order.
export interface FeInstructionMeta { id: string; help: string; parameters: string[] }
export interface FeParamMeta {
  id: string; name: string; type: 'NUMBER' | 'ENUM' | 'STRING' | 'String' | 'EXPRESSION'; defaultValue?: string | number
  required?: boolean; editable?: boolean; dict?: Record<string, string | number>; autocomplete?: boolean
}
export interface FeTemplateMeta { id: string; name: string; instructionIds: string[] }
// `layerIds` lists every Packet subclass (a layer id outside it is refused before the worker); `protocols` only the ones with a definition.
export interface Definitions {
  etag: string; layerIds: string[]; protocols: ProtocolDef[]
  feInstructions: FeInstructionMeta[]; feInstructionParameters: FeParamMeta[]; feParameters: FeParamMeta[]; feTemplates: FeTemplateMeta[]
}
export interface FieldEngineResult { instructions: Record<string, unknown>; error: string | null }
// A build error is a 200: `error` is set, `binary` is null and `data` empty. A Field Engine error leaves `binary` and `data` and sets `field_engine.error`.
export interface BuildResult { binary: string | null; data: PacketLayer[]; field_engine: FieldEngineResult | null; error: string | null }
export interface DocFromBinary { doc: PacketDoc; warnings: string[] }
export interface ReconstructEdit { doc: PacketDoc; warnings: string[] }
export interface Decompiled { instructions: FeInstruction[]; global_parameters: Record<string, FeValue>; warnings: string[] }
export interface ServerTemplate { id: string; name: string; buildable: boolean; reason: string | null }
export interface TemplateList { server: ServerTemplate[]; user: string[]; dir: string }
export interface PcapRow { index: number; length: number; summary: string }
export interface PcapPage { total: number; rows: PcapRow[]; warnings: string[] }
// Exactly one of path (on the backend host) and upload_id.
export type PcapSource = { path: string; upload_id?: undefined } | { upload_id: string; path?: undefined }
// A preset and the parameters its form starts from (IPv4 addresses are strings; whole-number parameters are JSON numbers).
export interface PresetInfo { id: string; name: string; defaults: Record<string, unknown> }
// `flags` is the stream's MAC-mode bits (bit 0: source MAC from the packet; bits 1-2: destination, 0 config, 1 packet), null when the preset leaves them alone.
export interface PresetResult { doc: PacketDoc; flags: number | null }
const packetPath = (rest: string) => `/api/studio/packet${rest}`
const templatePath = (kind: 'server' | 'user', name: string) => packetPath(`/templates/${kind}/${name.split('/').map(encodeURIComponent).join('/')}`)
export const packetDefinitions = () => call<Definitions>('GET', packetPath('/definitions'))
export const buildPacket = (doc: PacketDoc) => call<BuildResult>('POST', packetPath('/build'), { doc })
export const payloadClasses = (doc: PacketDoc) => call<{ classes: string[] }>('POST', packetPath('/payload-classes'), { doc })
export const paramValues = (doc: PacketDoc, paramId: string) => call<{ map: Record<string, string> }>('POST', packetPath('/param-values'), { doc, param_id: paramId })
export const docFromBinary = (binary: string, vm?: StreamJson['vm']) => call<DocFromBinary>('POST', packetPath('/doc-from-binary'), vm ? { binary, vm } : { binary })
// `edited` names the field the byte belongs to, or null when it belongs to none.
export const reconstructEdit = (doc: PacketDoc, binary: string, edited: { layer: number; field: string } | null) =>
  call<ReconstructEdit>('POST', packetPath('/reconstruct'), { doc, binary, edited })
export const decompileVm = (binary: string, vm: StreamJson['vm']) => call<Decompiled>('POST', packetPath('/decompile'), { binary, vm })
export const toStream = (doc: PacketDoc) => call<{ packet: { binary: string; meta: string }; vm: StreamJson['vm'] }>('POST', packetPath('/to-stream'), { doc })
export const vmPython = (doc: PacketDoc) => call<{ code: string }>('POST', packetPath('/vm-python'), { doc })
export const listTemplates = () => call<TemplateList>('GET', packetPath('/templates'))
export const serverTemplate = (id: string) => call<{ doc: PacketDoc }>('GET', templatePath('server', id))
export const userTemplate = (name: string) => call<{ doc: PacketDoc }>('GET', templatePath('user', name))
// 409 `exists` unless overwrite.
export const saveUserTemplate = (name: string, doc: PacketDoc, overwrite: boolean) => call<{ doc: PacketDoc }>('PUT', templatePath('user', name), { doc, overwrite })
export const deleteUserTemplate = (name: string) => call<Record<string, never>>('DELETE', templatePath('user', name))
export const listPresets = () => call<{ presets: PresetInfo[] }>('GET', packetPath('/presets'))
export const buildPreset = (id: string, params: Record<string, unknown>) => call<PresetResult>('POST', packetPath(`/presets/${encodeURIComponent(id)}`), params)
export const pcapList = (src: PcapSource, offset: number) => call<PcapPage>('POST', packetPath('/pcap/list'), { ...src, offset })
export const pcapPacket = (src: PcapSource, index: number) => call<{ binary: string; doc: PacketDoc; warnings: string[] }>('POST', packetPath('/pcap/packet'), { ...src, index })

// P5 Monitor. The `monitor` message (backend monitor/session.py snapshot; recorded in __fixtures__/monitorStl.json and
// monitorStateful.json): the latest value of each known PUB name, or null while that name has never arrived. The names
// avoid the capture monitor's monitorStart and monitorStop.
export interface MonitorTarget { host: string; port: number }
export type MonitorConnState = 'disconnected' | 'connecting' | 'connected' | 'stale'
export type MonitorGlobalStats = Record<string, number | number[] | { freq: number; value: number } | null>
export interface MonitorMsg {
  type: 'monitor'; state: MonitorConnState; target: MonitorTarget | null; alive: boolean
  frames: number; bytes: number; dropped_triggers: number
  dropped: { oversize: number; undecodable: number; other_names: number; long_name: number }
  ts: number | null; stale_since: number | null
  global: MonitorGlobalStats | null
  latency: Record<string, number> | null
  latency_v2: Record<string, unknown> | null
  rx_check: Record<string, unknown> | null
  templates: string[] | null
  known_ts: Record<string, number>
  other: Record<string, { ts: number; data?: unknown; data_text?: string; truncated?: boolean; bytes?: number }>
}
export const monitorConnect = (host: string, port: number) => call<MonitorMsg>('POST', '/api/monitor/connect', { host, port })
export const monitorDisconnect = () => call<MonitorMsg>('POST', '/api/monitor/disconnect')

// P5 Daemon. The `daemon` message is DaemonSession.snapshot() (backend daemon/session.py; recorded values in
// __fixtures__/daemonMsg.json and daemonMsgStf.json). `run` is the record of the run this backend started.
export interface DaemonHint { host: string; port: number }
export type DaemonConnState = 'disconnected' | 'connected' | 'error'
export interface DaemonRun { mode: 'stl' | 'stf'; seq: number; profile: string | null; duration_s: number | null; started_at: number
  state: 'running' | 'ended' | 'stopped'; outcome: string | null }
export interface DaemonMsg {
  type: 'daemon'; state: DaemonConnState; target: DaemonHint | null; version: string | null
  status: { state: string; verbose: string } | null; running_seq: number | null; files_path: string | null; last_error: string | null
  trex_cmds: { pid: number; cmd: string }[]; reserved: boolean; pushed: string[]; busy: boolean; run: DaemonRun | null
}
// The typed fields of a stateful run (backend StfBody); `m` is sent as a float.
export interface StfFields { profile: string; d: number; m: number; l: number; rx_check: number }
export interface StartBody { cfg_path: string; options: { c?: number }; trex_args: string; mode: 'stl' | 'stf'; stf?: StfFields }
export interface StfProfileInfo { path: string; duration: number | null; templates: string[]; errors: string[]; warnings: string[] }
export const daemonConnect = (host: string, port: number) => call<Record<string, unknown>>('POST', '/api/daemon/connect', { host, port })
export const daemonDisconnect = () => call<DaemonMsg>('POST', '/api/daemon/disconnect')
export const daemonStartPreview = (b: StartBody) => call<{ command_preview: string }>('POST', '/api/daemon/start/preview', b)
export const daemonStart = (b: StartBody & { timeout: number; confirm: boolean }) =>
  call<{ seq: number; command_preview: string; run: DaemonRun }>('POST', '/api/daemon/start', b)
export const daemonStop = (b: { rpc_port: number; confirm: boolean; force: boolean; owners_unknown_ack: boolean }) =>
  call<{ running: boolean; stop_s: number | null }>('POST', '/api/daemon/stop', b)
export const daemonStfProfiles = (source: 'stock' | 'uploaded') => call<{ source: string; files: string[] }>('GET', `/api/daemon/stf/profiles?source=${source}`)
export const daemonStfProfile = (path: string) => call<StfProfileInfo>('POST', '/api/daemon/stf/profile', { path })
// The STF upload (backend daemon/session.py stf_upload_preview, stf_upload). `path` is a file on the backend's host.
export interface UploadPreviewFile { template: string; local: string; remote: string; bytes: number; exists: boolean }
export interface UploadPreview {
  profile: string; files: UploadPreviewFile[]; total_bytes: number; yaml: string
  yaml_remote: string; yaml_bytes: number; yaml_exists: boolean; warnings: string[]
}
export const daemonStfUploadPreview = (path: string) => call<UploadPreview>('POST', '/api/daemon/stf/upload/preview', { path })
// `expected_yaml_remote` is the preview's yaml_remote and `expected_target` the daemon's host:port the confirm named; the backend
// answers 409 plan_changed when the files no longer plan to it or another daemon is connected.
export const daemonStfUpload = (b: { path: string; confirm: boolean; expected_yaml_remote: string; expected_target: string }) =>
  call<{ profile: string; pushed: string[]; skipped: string[] }>('POST', '/api/daemon/stf/upload', b)
export const daemonLog = (which: 'trex' | 'daemon') => call<{ which: string; text: string }>('GET', `/api/daemon/log?which=${which}`)

// The config preview and push (backend daemon/session.py preview, push). `values` is null when a YAML text did not parse.
export interface ConfigError { path: string; line: number | null; message: string }
export interface ConfigPreview { yaml: string; values: Record<string, unknown> | null; errors: ConfigError[] }
export const daemonConfigPreview = (b: { values: Record<string, unknown> } | { text: string }) =>
  call<ConfigPreview>('POST', '/api/daemon/config/preview', b)
export const daemonConfigPush = (b: { yaml: string; filename: string | null; confirm: boolean }) =>
  call<{ path: string }>('POST', '/api/daemon/config/push', b)
