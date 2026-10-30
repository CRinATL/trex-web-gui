import { formatRate, parseRate } from '../../rateText'
import type { StreamFlowStats, StreamJson, StreamMode, StreamPatch, StreamRate } from '../../api'

// The Properties form's values (A6.2, A6.3, A6.6): numbers are text while they are typed. The limits are the backend's
// (studio/streams.py), restated so a bad value is refused on its field before it is sent.

// The literal of backend/trex_web_gui/validation.py NAME_RE (R14, re-review I3).
export const NAME_RE = /^[\x20-\x21\x23-\x26\x28-\x5b\x5d-\x7e]{1,80}$/
const U32 = 4294967295
const DEFAULT_CACHE = '5000'

export type ModeType = StreamMode['type']
export type RxKind = 'none' | 'stats' | 'latency' | 'tpg'
export type StreamName = string | number

export interface StreamFormValues {
  name: string                          // '' = no name
  mode: ModeType
  totalPkts: string; pktsPerBurst: string; count: string; ibg: string
  rate: string                          // typed: '1.5 Mpps', '25%', '10 Gbps L1' (rateText.ts)
  enabled: boolean; selfStart: boolean; startPaused: boolean
  next: string                          // '' = Stop, else JSON.stringify(target name), so 1 and "1" stay apart
  actionCount: string; isg: string
  randomSeed: string                    // '' or 0 = none
  coreId: string                        // -1 = any
  srcMacFromPacket: boolean
  dstMac: string                        // '0' config file, '1' packet, '2' ARP (the flags bits 1-2)
  dummy: boolean
  rx: RxKind; pgId: string
  cacheOn: boolean; cacheSize: string
}

export const toForm = (s: StreamJson): StreamFormValues => {
  const m = s.mode, fs = s.flow_stats
  return {
    name: s.name === undefined ? '' : String(s.name),
    mode: m.type,
    totalPkts: String(m.total_pkts ?? 1), pktsPerBurst: String(m.pkts_per_burst ?? 1), count: String(m.count ?? 1), ibg: String(m.ibg ?? 0),
    rate: formatRate({ kind: m.rate.type, value: m.rate.value }),
    enabled: s.enabled, selfStart: s.self_start, startPaused: s.start_paused ?? false,
    next: s.next === undefined ? '' : JSON.stringify(s.next),
    actionCount: String(s.action_count), isg: String(s.isg),
    randomSeed: String(s.random_seed ?? 0),
    coreId: String(s.core_id ?? -1),
    srcMacFromPacket: (s.flags & 1) === 1, dstMac: String((s.flags >> 1) & 3), dummy: (s.flags & 8) === 8,
    rx: !fs.enabled ? 'none' : fs.rule_type ?? 'stats', pgId: fs.stream_id === undefined ? '' : String(fs.stream_id),
    cacheOn: s.vm.cache !== undefined, cacheSize: String(s.vm.cache ?? DEFAULT_CACHE),
  }
}

const sameName = (a: unknown, b: unknown) => a === b       // by value and type: 1 and "1" differ

// The named streams a `next` may go to (R10): not this one, not unnamed ones.
export function nextChoices(names: (StreamName | null)[], selfName: StreamName | null | undefined): StreamName[] {
  return names.filter((n): n is StreamName => n !== null && !sameName(n, selfName))
}

const FLOAT = /^-?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/      // 1, 1.5, 1. and .5
const INT = /^-?\d+$/
const isFloat = (t: string) => FLOAT.test(t.trim()) && Number.isFinite(Number(t))
const uint = (t: string, min: number, max: number) => INT.test(t.trim()) && Number(t) >= min && Number(t) <= max

// Why `name` may not be this stream's name, or undefined: the stored name unchanged is always fine; a new one must match
// NAME_RE and be free (by value and type). '' removes the name. The properties form and the table's inline rename share it.
export function nameError(name: string, names: (StreamName | null)[], selfName: StreamName | null | undefined): string | undefined {
  if (name === '') return undefined
  if (selfName !== null && selfName !== undefined && name === String(selfName)) return undefined
  if (!NAME_RE.test(name)) return 'Use 1 to 80 printable ASCII characters, without quotes or backslash.'
  if (names.some((n) => sameName(n, name))) return 'This name is taken by another stream.'
  return undefined
}

// Field key -> the message shown on that field; empty when the form can be saved. `names` is every stream's name in
// the profile (the Next choices and the uniqueness test), `selfName` this stream's stored name. `storedNext` is the
// stored stream's form-encoded `next`: keeping it unchanged is never an error (a one-packet pcap import loops to itself),
// only a newly chosen self or unknown target is.
export function formErrors(f: StreamFormValues, names: (StreamName | null)[], selfName: StreamName | null | undefined, storedNext?: string, storedRate?: string): Record<string, string> {
  const e: Record<string, string> = {}
  const nameProblem = nameError(f.name, names, selfName)
  if (nameProblem) e.name = nameProblem
  if (f.mode === 'single_burst' && !uint(f.totalPkts, 1, Infinity)) e.totalPkts = 'Enter a whole number, at least 1.'
  if (f.mode === 'multi_burst') {
    if (!uint(f.pktsPerBurst, 1, Infinity)) e.pktsPerBurst = 'Enter a whole number, at least 1.'
    if (!uint(f.count, 1, Infinity)) e.count = 'Enter a whole number, at least 1.'
    if (!isFloat(f.ibg) || Number(f.ibg) < 0) e.ibg = 'Enter a number of µs, 0 or more.'
  }
  // the stored rate text unchanged is never an error (it may not re-parse, e.g. 1e-7 pps)
  const rate = parseRate(f.rate, 'pps')
  if ('error' in rate && f.rate !== storedRate) e.rate = rate.error
  if (f.next !== '' && f.next !== storedNext) {
    const choices = nextChoices(names, selfName).map((n) => JSON.stringify(n))
    if (!choices.includes(f.next)) e.next = 'Choose another named stream, or Stop.'
  }
  if (!uint(f.actionCount, 0, U32)) e.actionCount = `Enter a whole number from 0 to ${U32}.`
  if (!isFloat(f.isg) || Number(f.isg) < 0) e.isg = 'Enter a number of µs, 0 or more.'
  if (f.randomSeed.trim() !== '' && !uint(f.randomSeed, 0, U32)) e.randomSeed = `Enter a whole number from 0 to ${U32}.`
  if (!uint(f.coreId, -1, 255)) e.coreId = 'Enter -1 (any core) or a core number up to 255.'
  else if ((f.rx === 'latency' || f.rx === 'tpg') && Number(f.coreId) >= 0) e.coreId = `A ${f.rx} stream cannot be pinned to a core: use -1.`
  if (!uint(f.dstMac, 0, 2)) e.dstMac = 'Choose where the destination MAC comes from.'
  if ((f.rx === 'stats' || f.rx === 'latency') && !uint(f.pgId, 0, U32)) e.pgId = `Enter a PG ID from 0 to ${U32}.`
  if (f.cacheOn && !uint(f.cacheSize, 1, 65535)) e.cacheSize = 'Enter a number of packets from 1 to 65535.'
  return e
}

function same(a: unknown, b: unknown): boolean {
  if (a === b) return true
  if (typeof a !== 'object' || typeof b !== 'object' || a === null || b === null) return false
  const ka = Object.keys(a), kb = Object.keys(b)
  return ka.length === kb.length && ka.every((k) => k in b && same((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k]))
}

// A typed Studio rate as a stream rate, or null when it is unreadable. With bare 'pps', parseRate never returns 'factor':
// it refuses a × mark (rateText.ts parseRate), so the kind is one of StreamRate's.
function typedRate(text: string): StreamRate | null {
  const r = parseRate(text, 'pps')
  return 'error' in r ? null : { type: r.kind as StreamRate['type'], value: r.value }
}

// The fields of `f` that differ from `base`, as a field-level patch. `packet` and `vm.instructions` are never
// edited: the only `vm` sent is `base.vm` with its `cache` set or removed (R11), instructions verbatim.
export function patchFrom(base: StreamJson, f: StreamFormValues): StreamPatch {
  const p: StreamPatch = {}
  const put = <K extends keyof StreamPatch>(k: K, v: StreamPatch[K], was: unknown) => { if (!same(v, was)) p[k] = v }

  const name = f.name === '' ? null : f.name
  if (name !== (base.name === undefined ? null : base.name) && !(base.name !== undefined && f.name === String(base.name))) p.name = name

  // the rate is compared by kind and value, so re-formatting the text never makes a patch; an unreadable one keeps the stored rate
  const typed = typedRate(f.rate)
  const untouched = f.rate === toForm(base).rate      // the stored rate itself, not its text round-tripped (16-digit values)
  const rate = untouched || !typed ? base.mode.rate : typed
  const mode: StreamMode = { type: f.mode, rate }
  if (f.mode === 'single_burst') mode.total_pkts = Number(f.totalPkts)
  if (f.mode === 'multi_burst') { mode.pkts_per_burst = Number(f.pktsPerBurst); mode.ibg = Number(f.ibg); mode.count = Number(f.count) }
  put('mode', mode, base.mode)

  put('enabled', f.enabled, base.enabled)
  put('self_start', f.selfStart, base.self_start)
  put('start_paused', f.startPaused, base.start_paused ?? false)

  const next = f.mode === 'continuous' || f.next === '' ? null : (JSON.parse(f.next) as StreamName)
  if (next !== (base.next === undefined ? null : base.next)) p.next = next
  put('action_count', Number(f.actionCount), base.action_count)
  put('isg', Number(f.isg), base.isg)

  const seed = f.randomSeed.trim() === '' ? 0 : Number(f.randomSeed)
  if (seed !== (base.random_seed ?? 0)) p.random_seed = seed === 0 ? null : seed
  put('core_id', Number(f.coreId), base.core_id ?? -1)
  put('flags', (f.srcMacFromPacket ? 1 : 0) | (Number(f.dstMac) << 1) | (f.dummy ? 8 : 0), base.flags)

  const was = toForm(base)
  if (f.rx !== 'tpg' && (f.rx !== was.rx || (f.rx !== 'none' && f.pgId !== was.pgId))) {
    const fs: StreamFlowStats = f.rx === 'none' ? { enabled: false } : { enabled: true, rule_type: f.rx, stream_id: Number(f.pgId) }
    if (f.rx !== 'none') for (const k of ['vxlan', 'ieee_1588'] as const) if (base.flow_stats[k] !== undefined) fs[k] = base.flow_stats[k]
    p.flow_stats = fs
  }

  if (f.cacheOn !== (base.vm.cache !== undefined) || (f.cacheOn && Number(f.cacheSize) !== base.vm.cache)) {
    const vm = { ...base.vm }
    delete vm.cache
    p.vm = f.cacheOn ? { ...vm, cache: Number(f.cacheSize) } : vm
  }
  return p
}

// The form fields Save would send (R6): each one whose value differs from `base` by the comparison patchFrom makes, so a
// field re-typed to an equal value (1pps over 1 pps, ISG 00 over 0) is not one. `mode` stands for the mode type; a burst
// field counts only in its own mode and against its stored (or default) value, so switching mode does not name the
// defaults it shows. The rate keeps patchFrom's rules: the untouched stored text and an unreadable text send nothing.
export function sentFields(base: StreamJson, f: StreamFormValues): Set<keyof StreamFormValues> {
  const was = toForm(base)
  const out = new Set<keyof StreamFormValues>()
  const num = (k: keyof StreamFormValues) => { if (Number(f[k]) !== Number(was[k])) out.add(k) }
  const flag = (k: keyof StreamFormValues) => { if (f[k] !== was[k]) out.add(k) }

  const name = f.name === '' ? null : f.name
  if (name !== (base.name === undefined ? null : base.name) && !(base.name !== undefined && f.name === String(base.name))) out.add('name')
  if (f.mode !== base.mode.type) out.add('mode')
  const typed = typedRate(f.rate)
  if (f.rate !== was.rate && typed && !same(typed, base.mode.rate)) out.add('rate')
  if (f.mode === 'single_burst') num('totalPkts')
  if (f.mode === 'multi_burst') { num('pktsPerBurst'); num('count'); num('ibg') }
  for (const k of ['enabled', 'selfStart', 'startPaused', 'srcMacFromPacket', 'dummy'] as const) flag(k)
  const next = f.mode === 'continuous' || f.next === '' ? null : (JSON.parse(f.next) as StreamName)
  if (next !== (base.next === undefined ? null : base.next)) out.add('next')
  num('actionCount'); num('isg'); num('coreId'); num('dstMac')
  const seed = f.randomSeed.trim() === '' ? 0 : Number(f.randomSeed)
  if (seed !== (base.random_seed ?? 0)) out.add('randomSeed')
  if (f.rx !== 'tpg') {
    if (f.rx !== was.rx) out.add('rx')
    if (f.rx !== 'none' && f.pgId !== was.pgId) out.add('pgId')     // patchFrom compares the PG ID's text
  }
  flag('cacheOn')
  if (f.cacheOn) num('cacheSize')
  return out
}
