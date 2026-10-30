import type { ClipboardEvent } from 'react'
import { useAppState } from './state'
import type { ConnectParams, PortLabelsMsg, PortView } from './api'
import { confirmedName } from './components/ifaceText'

export type PortLabels = PortLabelsMsg['labels']
// `inferred` marks a name nobody saved: the port's confirmed kernel interface name, shown until a name is saved.
export interface PortLabel { name: string; description: string; inferred?: boolean }

// Limits of backend/trex_web_gui/store/port_labels.py, counted in code points after trimming (not UTF-16 units).
export const NAME_MAX = 40
export const DESCRIPTION_MAX = 500
export const cpLength = (s: string) => [...s].length

const MAC = /^([0-9a-f]{2}:){5}[0-9a-f]{2}$/
const macOf = (p: PortView) => String(p.info.hw_mac ?? '').toLowerCase()
const usable = (m: string) => MAC.test(m) && m !== '00:00:00:00:00:00'

// The server string and the label key. A port without a usable MAC, or whose MAC another port of the same server repeats,
// is keyed by its id: a MAC key would give two ports one label.
export function portKey(params: ConnectParams, port: PortView, ports: PortView[]): { server: string; key: string } {
  const server = `${params.host}:${params.sync_port}`
  const mac = macOf(port)
  const unique = usable(mac) && !ports.some((o) => o !== port && o.id !== port.id && macOf(o) === mac)
  return { server, key: unique ? mac : `#${port.id}` }
}

export function portLabel(labels: PortLabels, params: ConnectParams | null, port: PortView, ports: PortView[]): PortLabel | null {
  if (!params) return null
  const { server, key } = portKey(params, port, ports)
  return labels[server]?.[key] ?? null
}

export const portTitle = (port: PortView, label: PortLabel | null): string =>
  label?.name ? `Port ${port.id} — ${label.name}` : `Port ${port.id}`

// Accessible name of a port heading: the title, then the description when there is one.
export const portAria = (port: PortView, label: PortLabel | null): string =>
  label?.description ? `${portTitle(port, label)}, ${label.description}` : portTitle(port, label)

// Label lookup bound to the current session and port list, for the components that print port headings.
export function usePortLabelOf(ports: PortView[]): (p: PortView) => PortLabel | null {
  const { portLabels, session } = useAppState()
  return (p) => portLabel(portLabels, session.params, p, ports)
}

// For headings and port lists: the saved label, else the confirmed kernel interface name as an inferred one (RC1-12).
// The name field keeps usePortLabelOf, so an inferred name is only ever a suggestion there, never a saved value.
export function usePortDisplayLabelOf(ports: PortView[]): (p: PortView) => PortLabel | null {
  const { portLabels, session } = useAppState()
  return (p) => {
    const saved = portLabel(portLabels, session.params, p, ports)
    if (saved?.name) return saved
    const name = confirmedName(session.ifaces ?? [], p.id)
    return name ? { name, description: saved?.description ?? '', inferred: true } : saved
  }
}

// The backend rejects any control character, a newline included, so the name fields are single-line; pasted line breaks become a space.
export const oneLine = (s: string) => s.replace(/\r\n|\r|\n/g, ' ')

// An <input> strips line breaks from pasted text before onChange sees them (jsdom, Firefox), so paste is handled here:
// the text goes in with each break turned into a space.
export function pasteInto(set: (v: string) => void) {
  return (e: ClipboardEvent<HTMLInputElement>) => {
    const text = e.clipboardData.getData('text')
    if (!/[\r\n]/.test(text)) return
    e.preventDefault()
    const el = e.currentTarget
    const start = el.selectionStart ?? el.value.length, end = el.selectionEnd ?? el.value.length
    set(el.value.slice(0, start) + oneLine(text) + el.value.slice(end))
  }
}
