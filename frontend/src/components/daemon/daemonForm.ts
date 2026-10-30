// The Run tab's form rules: the port prefills (re-review M7) and the bounds of the stateful run's typed fields.
// The bounds are the backend's (stfyaml STF_*_MIN/MAX, StfBody); the backend checks them again.
import type { StfFields } from '../../api'

export const DEFAULT_CFG_PATH = '/etc/trex_cfg.yaml'
export const DEFAULT_RPC_PORT = 4501   // TRex's own defaults when the server's hello carries none
export const DEFAULT_PUB_PORT = 4500
export const STF_D_MIN = 10
export const STF_D_MAX = 3600
export const STF_M_MAX = 1000
export const STF_L_MAX = 100000
export const STF_RX_CHECK_MAX = 65535

// What the dialog last pushed: the path in the daemon's files dir and the config's own values (Task 12's Push writes it).
export interface PushedConfig { path: string; values: { zmq_rpc_port?: number; zmq_pub_port?: number } }

// The last pushed config of each daemon, kept for the page's life (as the dialog's last target is): the dialog remounts on every
// open, and the Run tab's config path and port prefills follow what the dialog last pushed to the daemon it is talking to.
// Keyed by host:port, so a Push to one daemon never prefills another's Run tab.
export interface DaemonTarget { host: string; port: number }
const pushedMemory = new Map<string, PushedConfig>()
const targetKey = (t: DaemonTarget) => `${t.host}:${t.port}`
export const getLastPushed = (t: DaemonTarget | null): PushedConfig | null => (t ? pushedMemory.get(targetKey(t)) ?? null : null)
export function rememberPushed(t: DaemonTarget, p: PushedConfig): void { pushedMemory.set(targetKey(t), p) }
export function forgetPushed(): void { pushedMemory.clear() }

export type ServerPorts = { sync_port: number; async_port: number } | null

// The command port prefill: the pushed config's zmq_rpc_port when the config path is the one last pushed, else the Control
// connect's port, else 4501. A wrong port makes Stop's owner read fail (owners_unknown), so this is load-bearing.
export function cmdPortPrefill(cfgPath: string, pushed: PushedConfig | null, ports: ServerPorts): number {
  if (pushed && pushed.path === cfgPath && pushed.values.zmq_rpc_port !== undefined) return pushed.values.zmq_rpc_port
  return ports?.sync_port ?? DEFAULT_RPC_PORT
}

// The same for the Monitor port: zmq_pub_port, else the Control connect's event port, else 4500.
export function monitorPortPrefill(cfgPath: string, pushed: PushedConfig | null, ports: ServerPorts): number {
  if (pushed && pushed.path === cfgPath && pushed.values.zmq_pub_port !== undefined) return pushed.values.zmq_pub_port
  return ports?.async_port ?? DEFAULT_PUB_PORT
}

// The text of a whole number in lo..hi, or null.
export function intIn(text: string, lo: number, hi: number): number | null {
  const t = text.trim()
  if (!/^\d+$/.test(t)) return null
  const n = Number(t)
  return n >= lo && n <= hi ? n : null
}

// A profile's YAML duration as the Duration field wants it: rounded, then clamped to 10..3600.
export const clampDuration = (seconds: number): number => Math.min(STF_D_MAX, Math.max(STF_D_MIN, Math.round(seconds)))

// The stateful form as typed. monitorPort is null while it follows the prefill.
export interface StfForm { profile: string; d: string; m: string; l: string; rxCheck: string; monitorPort: string | null }
export const NEW_STF_FORM: StfForm = { profile: '', d: '', m: '1', l: '0', rxCheck: '0', monitorPort: null }

export interface StfProblems { d?: string; m?: string; l?: string; rxCheck?: string; monitorPort?: string }

// One message per field that breaks its bound; empty when the form is valid. `monitorPort` is the value shown (typed or prefilled).
export function stfProblems(f: StfForm, monitorPort: string): StfProblems {
  const out: StfProblems = {}
  if (intIn(f.d, STF_D_MIN, STF_D_MAX) === null) out.d = `Duration must be a whole number of seconds, ${STF_D_MIN}–${STF_D_MAX}`
  const m = Number(f.m)
  if (f.m.trim() === '' || !Number.isFinite(m) || !(m > 0 && m <= STF_M_MAX)) out.m = `Multiplier must be above 0 and at most ${STF_M_MAX}`
  if (intIn(f.l, 0, STF_L_MAX) === null) out.l = `Latency must be a whole number of pps, 0–${STF_L_MAX}`
  if (intIn(f.rxCheck, 0, STF_RX_CHECK_MAX) === null) out.rxCheck = `Rx-check must be a whole number, 0–${STF_RX_CHECK_MAX}`
  if (intIn(monitorPort, 1, 65535) === null) out.monitorPort = 'Monitor port must be 1–65535'
  return out
}

// The request's `stf` object from a valid form; null when the form has a problem or no profile.
export function stfFields(f: StfForm, monitorPort: string): StfFields | null {
  if (!f.profile || Object.keys(stfProblems(f, monitorPort)).length) return null
  return { profile: f.profile, d: Number(f.d), m: Number(f.m), l: Number(f.l), rx_check: Number(f.rxCheck) }
}

// A byte count for the upload dialogs: bytes under 1 KiB, else one decimal of KiB or MiB.
export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KiB`
  return `${(n / 1024 / 1024).toFixed(1)} MiB`
}
