// The Daemon dialog's config model: the daemon's own field metadata (get_trex_config_metadata, recorded in
// __fixtures__/daemonMetadata.json), the values being edited, and the pure edits on them. The backend validates (daemon/config.py);
// nothing here knows a field's rule, only its shape.
import type { ConfigError } from '../../api'

export type MetaType = 'NUMBER' | 'FLOAT' | 'STRING' | 'BOOLEAN' | 'LIST' | 'OBJECT' | 'IP' | 'MAC'
// One field of the metadata. A LIST's `item` has no `id`.
export interface MetaField {
  id?: string; name: string; type: MetaType; mandatory?: boolean; default?: unknown; description?: string
  item?: MetaField; attributes?: MetaField[]
}
export type Values = Record<string, unknown>
export type Path = (string | number)[]

// Spec §10: these sit under the Advanced fold. Every other field, including one this list has never heard of, shows by default.
const ADVANCED = new Set(['memory', 'platform', 'tw', 'prefix', 'limit_memory', 'c', 'port_bandwidth_gb', 'enable_zmq_pub'])
export const isAdvanced = (f: MetaField): boolean => f.id !== undefined && ADVANCED.has(f.id)

export const pathKey = (path: Path): string => path.join('.')

export function getAt(root: unknown, path: Path): unknown {
  let at: unknown = root
  for (const seg of path) {
    if (at === null || typeof at !== 'object') return undefined
    at = (at as Record<string | number, unknown>)[seg]
  }
  return at
}

// A copy of `root` with `value` at `path`; nothing is mutated and untouched branches are shared. A missing step is created
// (an array before a number, else an object). `undefined` removes an object key.
export function setAt(root: Values, path: Path, value: unknown): Values {
  if (path.length === 0) return root
  const [head, ...rest] = path
  const cur = (root as Record<string | number, unknown>)[head]
  const next = rest.length === 0 ? value : setAt((cur !== null && typeof cur === 'object' ? cur : typeof rest[0] === 'number' ? [] : {}) as Values, rest, value)
  if (Array.isArray(root)) { const copy = [...root]; copy[head as number] = next; return copy as unknown as Values }
  const copy = { ...root }
  if (next === undefined) delete copy[head as string]
  else copy[head as string] = next
  return copy
}

const listAt = (root: Values, path: Path): unknown[] => { const l = getAt(root, path); return Array.isArray(l) ? l : [] }
export const addItem = (root: Values, path: Path, item: unknown): Values => setAt(root, path, [...listAt(root, path), item])
export const removeItem = (root: Values, path: Path, index: number): Values => setAt(root, path, listAt(root, path).filter((_, i) => i !== index))

// The message of the error at exactly this path (backend paths are dotted: `port_info.0.default_gw`), or null.
export const fieldError = (errors: ConfigError[], path: Path): string | null => errors.find((e) => e.path === pathKey(path))?.message ?? null

// The values every field with a default starts at (backend config.defaults): a mandatory OBJECT's default is its attributes'
// defaults; an optional OBJECT has none and `c` none (NO_DEFAULT), so TRex's own apply (F1: a `platform` stub and `c: 4`
// are refused, E0 on the guest, 2026-10-26).
export const NO_DEFAULT: ReadonlySet<string> = new Set(['c'])
function defaultOf(f: MetaField): { has: boolean; value?: unknown } {
  if (f.default !== undefined) return { has: true, value: f.default }
  if (f.type === 'OBJECT' && f.mandatory === true) { const sub = defaultsOf(f.attributes ?? []); return Object.keys(sub).length ? { has: true, value: sub } : { has: false } }
  return { has: false }
}
export function defaultsOf(fields: MetaField[]): Values {
  const out: Values = {}
  for (const f of fields) {
    if (f.id === undefined || NO_DEFAULT.has(f.id)) continue
    const d = defaultOf(f)
    if (d.has) out[f.id] = d.value
  }
  return out
}

// What Add puts in a list: an empty entry of the item's type.
export function blankItem(item: MetaField): unknown {
  switch (item.type) {
    case 'OBJECT': return defaultsOf(item.attributes ?? [])
    case 'NUMBER': case 'FLOAT': return 0
    case 'BOOLEAN': return false
    case 'LIST': return []
    default: return ''
  }
}

// A device the interface picker offers: the value to write into `interfaces`, and a line describing it.
export interface DeviceOption { id: string; label: string }
export interface DaemonInfo { metadata: MetaField[]; metadataVersion: string; devices: DeviceOption[]; configYaml: string; filesPath: string | null }

// The connect reply's metadata, devices (plus the backend's own `dummy`), the daemon's current config and its files dir; null
// when the reply has no metadata (or a malformed one). A device's id is the slot in dpdk_setup_ports form (`00:03.0`).
export function readConnectInfo(r: Record<string, unknown>): DaemonInfo | null {
  if (!Array.isArray(r.metadata)) return null
  const devices: DeviceOption[] = []
  for (const d of Array.isArray(r.devices) ? r.devices : []) {
    if (d === null || typeof d !== 'object') continue
    const dev = d as Record<string, unknown>
    const id = [dev.id, dev.Slot_str, dev.Slot].find((x): x is string => typeof x === 'string' && x !== '')
    if (id === undefined) continue
    const text = [dev.Interface, dev.Device_str, dev.MAC, dev.Active].filter((x): x is string => typeof x === 'string' && x !== '')
    devices.push({ id, label: text.length ? `${id} ${text.join(' · ')}` : id })
  }
  return {
    metadata: r.metadata as MetaField[], metadataVersion: typeof r.metadata_version === 'string' ? r.metadata_version : '',
    devices, configYaml: typeof r.config_yaml === 'string' ? r.config_yaml : '', filesPath: typeof r.files_path === 'string' ? r.files_path : null,
  }
}
