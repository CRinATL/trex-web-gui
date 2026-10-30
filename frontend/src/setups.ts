import * as api from './api'
import type { ConnectParams, Inspected, LayerBody, PortView, SetupLayer, SetupPort, TestSetup } from './api'
import { dashboardPrefs, type DashboardPrefs } from './dashboardPrefs'
import type { LayerView } from './layerView'
import { PGID_PALETTE, SELECT_MAX } from './pgidPalette'
import { pgidStore } from './pgidStats'
import { isRunning } from './portActions'
import { portKey } from './portLabels'
import { formatRate, formMult, fromWireMult } from './rateText'
import { runInProgress } from './setupSession'
import { defaultsFor, parseMult, tunablesToArgs } from './traffic'
import { trafficStore, type TrafficForm } from './trafficStore'

// Mirror of backend store/test_setups.py slugify: lower-case, each run outside [a-z0-9] becomes one "-", ends trimmed.
// The PUT path must be exactly this (the server answers 422 "slug does not match name" otherwise).
export const slugify = (name: string): string => name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')

const kindOf = (form: TrafficForm, path: string): Inspected['kind'] => {
  if (form.inspected && form.inspected.path === path) return form.inspected.kind
  return /\.ya?ml$/i.test(path) ? 'yaml' : /\.json$/i.test(path) ? 'json' : 'py'
}

const scalars = (values: Record<string, unknown>): Record<string, unknown> =>
  Object.fromEntries(Object.entries(values).filter(([, v]) => ['string', 'number', 'boolean'].includes(typeof v)))

// A form's duration in seconds (-1: no limit); null when it is neither empty nor a positive number.
export function durationOf(form: TrafficForm): number | null {
  if (form.duration.trim() === '') return -1
  const d = Number(form.duration)
  return Number.isFinite(d) && d > 0 ? d : null
}

// Why a port's form cannot be saved in a setup, or null. The Save dialog names it and does not offer the port.
export function formProblem(form: TrafficForm): string | null {
  if (formMult(form.multValue, form.unit) === null) return 'multiplier is not valid'
  if (durationOf(form) === null) return 'duration is not valid'
  return null
}

// What `layerView` shows for a port, as the setup stores it (LayerBody without `port`). null when the view lacks what
// LayerBody needs (an L2 port with no destination, an L3 port with no addresses), so Run would not be able to apply it.
export function layerOf(v: LayerView): SetupLayer | null {
  const ipv6 = { enabled: v.ipv6 !== 'off', src: v.ipv6 === 'off' || v.ipv6 === 'auto' ? null : v.ipv6 }
  if (v.mode === 'L2') return v.dstMac ? { mode: 'L2', dst_mac: v.dstMac, src_ipv4: null, dst_ipv4: null, vlan: v.vlan, ipv6 } : null
  return v.srcIpv4 && v.dstIpv4 ? { mode: 'L3', dst_mac: null, src_ipv4: v.srcIpv4, dst_ipv4: v.dstIpv4, vlan: v.vlan, ipv6 } : null
}

// The setup for the ticked ports (`include`, port ids) of the Traffic forms. `layers` holds layerView(port) per port id and is
// read only when options.includeLayer. A port with no profile or an invalid form is left out.
export function buildSetup(name: string, params: ConnectParams, ports: PortView[], forms: Record<number, TrafficForm>,
  layers: Record<number, LayerView>, dash: { prefs: DashboardPrefs; streams: number[]; latency: number[] } | null,
  include: Set<number>, options: { includeLayer: boolean; stopFirst: boolean }, server: string | null): TestSetup {
  const out: SetupPort[] = []
  for (const p of ports) {
    const form = forms[p.id]
    if (!include.has(p.id) || !form?.formPath || formProblem(form)) continue
    out.push({
      key: portKey(params, p, ports).key, port: p.id,
      profile: { path: form.formPath, kind: kindOf(form, form.formPath) },
      tunables: { values: scalars(form.values), extra: form.extra },
      mult: formMult(form.multValue, form.unit) as string, duration: durationOf(form) as number, force: form.force,
      acquire_force: false, stop_first: options.stopFirst,
      layer: options.includeLayer && layers[p.id] ? layerOf(layers[p.id]) : null,
    })
  }
  return {
    name: name.trim(), slug: slugify(name), created: '', updated: '', server, ports: out,
    dashboard: dash && {
      tab: dash.prefs.tab, window_s: dash.prefs.windowS, layout: dash.prefs.layout,
      streams_pgids: dash.streams.slice(0, SELECT_MAX), latency_pgids: dash.latency.slice(0, SELECT_MAX),
    },
  }
}

export interface PortMatch { port: SetupPort; view: PortView | null; reason: string | null }

// Each setup port against the server's ports: by its key (the label key), else by port number. `view` null means the
// server does not have the port ("not on this server"); a match by number carries a note in `reason`.
export function matchPorts(setup: TestSetup, params: ConnectParams, ports: PortView[]): PortMatch[] {
  // Keys first for every setup port, so a number fallback never takes a server port another setup port matched by key.
  const claimed = new Set<number>()
  const byKey = setup.ports.map((port) => {
    const v = ports.find((p) => portKey(params, p, ports).key === port.key)
    if (v && !claimed.has(v.id)) { claimed.add(v.id); return v }
    return undefined
  })
  return setup.ports.map((port, i): PortMatch => {
    const k = byKey[i]
    if (k) return { port, view: k, reason: null }
    const byNumber = ports.find((p) => p.id === port.port)
    if (!byNumber) return { port, view: null, reason: 'not on this server' }
    if (claimed.has(byNumber.id)) return { port, view: null, reason: `port ${byNumber.id} is already used by another port of this setup` }
    claimed.add(byNumber.id)
    return { port, view: byNumber, reason: `matched by port number (no port with key ${port.key})` }
  })
}

// JSON with every object's keys sorted, so equal content gives equal text whatever order the server wrote the keys in.
export function stableStringify(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(stableStringify).join(',')}]`
  if (v !== null && typeof v === 'object') {
    const o = v as Record<string, unknown>
    return `{${Object.keys(o).sort().filter((k) => o[k] !== undefined).map((k) => `${JSON.stringify(k)}:${stableStringify(o[k])}`).join(',')}}`
  }
  return JSON.stringify(v) ?? 'null'
}

// The setup's content without its timestamps: what a Load inspected and what a Run would execute.
export const fingerprint = (s: TestSetup): string => {
  return stableStringify({ ...s, created: undefined, updated: undefined })
}

export const CHANGED_SINCE_LOAD = 'The setup changed since it was loaded; Load it again'

// Why a Load cannot back a Run of `snapshot` given the list's setups now, or null. The list's entry for the slug must still
// exist and have the content the Load saw: a hand-edited file, or a delete and re-import, can keep the slug and `updated`.
export function staleReason(snapshot: TestSetup, load: LoadResult, list: TestSetup[]): string | null {
  const now = list.find((s) => s.slug === snapshot.slug)
  if (load.slug !== snapshot.slug || load.fingerprint !== fingerprint(snapshot) || !now || fingerprint(now) !== load.fingerprint) return CHANGED_SINCE_LOAD
  return null
}

// The Traffic-form fields a Load writes and a Run uses (not the inspected specs): what the dialog compares edits against.
export type LoadedForm = Pick<TrafficForm, 'path' | 'formPath' | 'values' | 'extra' | 'multValue' | 'unit' | 'duration' | 'force'>

const rateText = (value: string, unit: TrafficForm['unit']): string => {
  const r = fromWireMult(`${value}${unit === 'x' ? '' : unit}`)
  return r ? formatRate(r) : (unit === 'x' || unit === '%' ? `${value}${unit}` : `${value} ${unit}`)
}
const durationText = (d: string): string => (d.trim() === '' ? 'no limit' : `${d.trim()} s`)
const valueText = (v: unknown): string => (v === undefined ? 'unset' : String(v))
// The Traffic tab's inputs store strings where Load wrote numbers, so scalars compare by their text; a boolean stays a boolean.
const sameValue = (a: unknown, b: unknown): boolean =>
  typeof a === 'boolean' || typeof b === 'boolean' ? a === b : a === undefined || b === undefined || a === null || b === null ? a === b : String(a) === String(b)

// What differs between the form Load wrote and the form now, each as "<field> <old> → <new>" (empty when nothing differs).
export function formDrift(loaded: LoadedForm, now: TrafficForm): string[] {
  const out: string[] = []
  if (loaded.formPath !== now.formPath) out.push(`profile ${loaded.formPath ?? 'none'} → ${now.formPath ?? 'none'}`)
  const was = formMult(loaded.multValue, loaded.unit) ?? `${loaded.multValue}${loaded.unit}`
  const is = formMult(now.multValue, now.unit) ?? `${now.multValue}${now.unit}`
  if (was !== is) out.push(`rate ${rateText(loaded.multValue, loaded.unit)} → ${rateText(now.multValue, now.unit)}`)
  if (loaded.duration.trim() !== now.duration.trim()) out.push(`duration ${durationText(loaded.duration)} → ${durationText(now.duration)}`)
  if (loaded.force !== now.force) out.push(`force ${loaded.force ? 'on' : 'off'} → ${now.force ? 'on' : 'off'}`)
  for (const k of new Set([...Object.keys(loaded.values), ...Object.keys(now.values)])) {
    if (!sameValue(loaded.values[k], now.values[k])) out.push(`${k} ${valueText(loaded.values[k])} → ${valueText(now.values[k])}`)
  }
  if (loaded.extra !== now.extra) out.push(`extra ${loaded.extra || 'none'} → ${now.extra || 'none'}`)
  return out
}

export interface LoadResult {
  slug: string; updated: string; ok: boolean
  forms: Record<number, LoadedForm>   // by port id: what this Load wrote to the Traffic forms (empty when the Load failed)
  fingerprint: string      // fingerprint() of the setup that was loaded: Run refuses unless the list still holds that content
  generation: number       // trafficStore.generation() when the Load began: a new connection invalidates it
  ports: { id: number; error: string | null; inspected: Inspected | null }[]
  serverMismatch: string | null
}

const errText = (x: unknown) => (x instanceof Error ? x.message : String(x))

// Inspects every matched port (this runs a .py profile in the profile worker). Forms and the dashboard change only when every
// port inspected, so a failed Load leaves everything as it was.
export async function loadSetup(setup: TestSetup, params: ConnectParams, ports: PortView[], deps: { inspect: typeof api.inspect }): Promise<LoadResult> {
  const generation = trafficStore.generation()
  const here = `${params.host}:${params.sync_port}`
  const results: LoadResult['ports'] = []
  const forms: { id: number; form: Partial<TrafficForm> & LoadedForm }[] = []
  for (const m of matchPorts(setup, params, ports)) {
    const sp = m.port
    if (!m.view) { results.push({ id: sp.port, error: m.reason, inspected: null }); continue }
    const id = m.view.id
    const mult = parseMult(sp.mult)
    if (!mult) { results.push({ id, error: `multiplier ${sp.mult} cannot be shown in the Traffic form`, inspected: null }); continue }
    try {
      const specs = (await deps.inspect({ path: sp.profile.path, ports: [id], tunables: [] })).tunables
      const values = { ...defaultsFor(specs), ...sp.tunables.values }
      const inspected = await deps.inspect({ path: sp.profile.path, ports: [id], tunables: tunablesToArgs(specs, values, sp.tunables.extra) })
      results.push({ id, error: null, inspected })
      forms.push({ id, form: {
        path: sp.profile.path, formPath: sp.profile.path, inspected, specs: inspected.tunables, values: { ...defaultsFor(inspected.tunables), ...sp.tunables.values },
        extra: sp.tunables.extra, multValue: mult.value, unit: mult.unit, duration: sp.duration === -1 ? '' : String(sp.duration), force: sp.force,
        pending: null,   // a running live-update countdown would fire the old multiplier and record it as applied
      } })
    } catch (x) {
      results.push({ id, error: errText(x), inspected: null })
    }
  }
  const ok = results.every((r) => r.error === null)
  const wrote: Record<number, LoadedForm> = {}
  if (ok) {
    // a connect_seq reset during the inspects cleared the stores: writing now would put a half form into the new connection
    if (trafficStore.generation() !== generation) throw new Error('The connection changed while loading; Load again')
    for (const f of forms) trafficStore.set(f.id, f.form)
    for (const f of forms) {
      const { path, formPath, values, extra, multValue, unit, duration, force } = f.form
      wrote[f.id] = { path, formPath, values, extra, multValue, unit, duration, force }
    }
    const d = setup.dashboard
    if (d) {
      dashboardPrefs.set({ tab: d.tab, windowS: d.window_s, layout: d.layout })
      for (const [tab, ids] of [['streams', d.streams_pgids], ['latency', d.latency_pgids]] as const) {
        pgidStore.setSelection(tab, { picked: ids.slice(0, SELECT_MAX).map((id, i) => ({ id, color: PGID_PALETTE[i] })), seeded: true })
      }
    }
  }
  return {
    slug: setup.slug, updated: setup.updated, fingerprint: fingerprint(setup), ok, forms: wrote, generation, ports: results,
    serverMismatch: setup.server !== null && setup.server !== here ? `This setup was saved for ${setup.server}; you are connected to ${here}` : null,
  }
}

export type RunStep = 'acquire' | 'stop' | 'layer' | 'assign' | 'start'
export interface RunFailure { id: number; step: RunStep; message: string; code: string }
export interface RunResult { acquired: number[]; started: number[]; failed: RunFailure | null; warnings: string[] }
export interface RunDeps {
  current: () => TestSetup[]     // the list's setups now, read at Run: the Loaded snapshot must still be among them unchanged
  ports: () => PortView[]        // the caller's current port list, read when used: the presence check and the transmitting test
  acquire: typeof api.acquire; stop: typeof api.stop; applyLayer: typeof api.applyLayer; assign: typeof api.assign; start: typeof api.start
}
// A step begins for these ports, or has completed for them.
export type StepEvent = { step: RunStep; ports: number[]; phase: 'begin' | 'done' }

const layerBody = (port: number, l: SetupLayer): LayerBody => ({
  port, mode: l.mode, vlan: l.vlan,
  ...(l.dst_mac ? { dst_mac: l.dst_mac } : {}), ...(l.src_ipv4 ? { src_ipv4: l.src_ipv4 } : {}), ...(l.dst_ipv4 ? { dst_ipv4: l.dst_ipv4 } : {}),
  ...(l.ipv6 ? { ipv6: l.ipv6 } : {}),
})

// Runs the Loaded snapshot `setup` (never the list's entry) in phases, so nothing transmits until every check has passed: acquire every port, stop the stop_first
// ports that transmit, apply every layer, assign every port, then start (one call per distinct mult/duration/force).
// It stops at the first error and returns what was acquired and started; nothing is rolled back. It throws, before any RPC,
// when the Load is not a good Load of this version of the setup, a port is gone, or another Run is in progress.
// `resume` continues after a refused acquire: the ports in `acquired` are skipped and `force` is acquired with force.
export async function runSetup(setup: TestSetup, load: LoadResult, deps: RunDeps, onStep: (e: StepEvent) => void,
  resume?: { acquired: number[]; force: number }): Promise<RunResult> {
  if (runInProgress.get()) throw new Error('A Run is already in progress in this tab')
  if (!load.ok) throw new Error('Load the setup first: the last Load did not succeed')
  const stale = staleReason(setup, load, deps.current())
  if (stale) throw new Error(stale)
  if (load.generation !== trafficStore.generation()) throw new Error('The connection changed since Load; Load again')
  const live = deps.ports().map((p) => p.id)
  for (const l of load.ports) if (!live.includes(l.id)) throw new Error(`Port ${l.id} is not on this server`)
  runInProgress.set(true)     // synchronously: no await has happened yet
  try {
    const result: RunResult = { acquired: [...(resume?.acquired ?? [])], started: [], failed: null, warnings: [] }
    if (load.serverMismatch) result.warnings.push(load.serverMismatch)
    const fail = (id: number, step: RunStep, x: unknown, code?: string): RunResult => {
      result.failed = { id, step, message: errText(x), code: code ?? (x instanceof api.ApiError ? x.code : '') }
      return result
    }
    // one RPC between a begin and a done event; its throw becomes the Run's failure (onStep's own throw is not caught)
    async function step<T>(name: RunStep, ids: number[], call: () => Promise<T>): Promise<{ ok: true; value: T } | { ok: false }> {
      // a reconnect during the Run: the ports and the Load belong to the old connection, so nothing more is sent
      if (trafficStore.generation() !== load.generation) {
        fail(ids[0], name, 'The connection changed during Run; nothing more was sent', 'connection_changed')
        return { ok: false }
      }
      onStep({ step: name, ports: ids, phase: 'begin' })
      let value: T
      try { value = await call() } catch (x) { fail(ids[0], name, x); return { ok: false } }
      onStep({ step: name, ports: ids, phase: 'done' })
      return { ok: true, value }
    }
    const idOf = (i: number) => load.ports[i].id
    let view = deps.ports()

    for (let i = 0; i < setup.ports.length; i++) {
      const id = idOf(i)
      if (resume?.acquired.includes(id)) continue
      const force = id === resume?.force ? true : setup.ports[i].acquire_force
      const r = await step('acquire', [id], () => deps.acquire({ ports: [id], force }))
      if (!r.ok) return result
      view = r.value.ports
      result.acquired.push(id)
    }

    const running = (id: number) => { const v = view.find((x) => x.id === id); return v !== undefined && isRunning(v) }
    const toStop = setup.ports.flatMap((p, i) => (p.stop_first && running(idOf(i)) ? [idOf(i)] : []))
    if (toStop.length && !(await step('stop', toStop, () => deps.stop({ ports: toStop }))).ok) return result

    for (let i = 0; i < setup.ports.length; i++) {
      const layer = setup.ports[i].layer
      if (!layer) continue
      const id = idOf(i)
      const r = await step('layer', [id], () => deps.applyLayer(layerBody(id, layer)))
      if (!r.ok) return result
      if (r.value.arp?.state === 'unresolved') result.warnings.push(`Port ${id}: ARP for ${layer.dst_ipv4} did not resolve; traffic to it will be dropped until it does`)
      if (r.value.service_restore_error) result.warnings.push(`Port ${id}: ${r.value.service_restore_error}`)
    }

    for (let i = 0; i < setup.ports.length; i++) {
      const sp = setup.ports[i], id = idOf(i)
      const inspected = load.ports[i].inspected
      const tunables = tunablesToArgs(inspected?.tunables ?? [], sp.tunables.values, sp.tunables.extra)
      const r = await step('assign', [id], () => deps.assign({ ports: [id], path: sp.profile.path, tunables, stop_first: sp.stop_first }))
      if (!r.ok) return result
    }

    const groups = new Map<string, { ports: number[]; mult: string; duration: number; force: boolean }>()
    setup.ports.forEach((p, i) => {
      const k = `${p.mult}|${p.duration}|${p.force}`
      const g = groups.get(k) ?? { ports: [], mult: p.mult, duration: p.duration, force: p.force }
      g.ports.push(idOf(i))
      groups.set(k, g)
    })
    for (const g of groups.values()) {
      const r = await step('start', g.ports, () => deps.start({ ports: g.ports, mult: g.mult, duration: g.duration, force: g.force }))
      if (!r.ok) return result
      result.started.push(...g.ports)
    }
    return result
  } finally {
    runInProgress.set(false)
  }
}
