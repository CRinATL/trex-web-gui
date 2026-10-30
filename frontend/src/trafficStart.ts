import { start, type PortView } from './api'
import { actionsFor, type Selection } from './portActions'
import { formatRate, formMult, fromWireMult } from './rateText'
import { parseMult } from './traffic'
import { trafficStore, type TrafficForm } from './trafficStore'

// A form the user has to fix before Start can run; the message is shown as is.
export class FormError extends Error {}

// The wire values of a form, or the first problem that stops a Start. The error texts are the ones the Traffic tab's alert line shows.
function parseForm(form: TrafficForm): { mult: string; duration: number } | { error: string } {
  const mult = formMult(form.multValue, form.unit)
  if (!mult) return { error: 'Enter a multiplier greater than 0' }
  const duration = form.duration.trim() === '' ? -1 : Number(form.duration)
  if (!Number.isFinite(duration) || (duration <= 0 && duration !== -1)) return { error: 'Duration must be a positive number of seconds, or empty for no limit' }
  return { mult, duration }
}

// The first problem that stops a Start, or null.
export function formError(form: TrafficForm): string | null {
  const r = parseForm(form)
  return 'error' in r ? r.error : null
}

// The rate part of a form, as Start would send it: "×1", "10%", "5 kpps".
export function rateText(form: TrafficForm): string {
  const value = form.multValue.trim()
  const r = fromWireMult(`${value}${form.unit === 'x' ? '' : form.unit}`)
  if (r) return formatRate(r)
  return form.unit === 'x' ? `×${value}` : form.unit === '%' ? `${value}%` : `${value} ${form.unit}`
}

// What a Start click will send, read from the form: "Start at 5 kpps, no limit", "Start at ×1, 30 s, force".
// An invalid form shows the problem instead. The toolbar's Start tooltip and the Traffic tab's summary line share it.
export function startSummary(form: TrafficForm): string {
  const bad = formError(form)
  if (bad) return bad
  const limit = form.duration.trim() === '' ? 'no limit' : `${form.duration.trim()} s`
  return `Start at ${rateText(form)}, ${limit}${form.force ? ', force' : ''}`
}

// Who may change a port's rate live: the rule that enables Update in the menu (connected, writable, owned, default profile transmitting).
export function canUpdateRate(selection: Selection, ports: PortView[], readOnly: boolean, connected: boolean): boolean {
  return !!actionsFor(selection, ports, readOnly, connected).find((a) => a.id === 'update')?.enabled
}

// Starts one port from its stored form (the Traffic tab and the toolbar share this): throws FormError for a bad form
// and otherwise drops the pending live update, calls start, and records the sent multiplier as applied.
export async function startFromForm(port: number): Promise<void> {
  const form = trafficStore.get(port)
  const r = parseForm(form)
  if ('error' in r) throw new FormError(r.error)
  dropPending([port])
  const g = trafficStore.generation()   // a reply that lands after a connect_seq reset must write nothing
  await start({ ports: [port], mult: r.mult, duration: r.duration, ...(form.force ? { force: true } : {}) })
  const sent = parseMult(r.mult)   // what the box would show for the sent text, as applyUpdate records it
  if (trafficStore.generation() === g && sent) trafficStore.set(port, { applied: { value: sent.value, unit: sent.unit } })
}

// Clears the pending live-update countdown of each port (Pause, Stop and Start do this so Resume never fires an old update).
export function dropPending(ports: number[]): void {
  for (const p of ports) if (trafficStore.get(p).pending) trafficStore.set(p, { pending: null })
}
