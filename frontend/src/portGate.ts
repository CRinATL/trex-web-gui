import type { BusyKind, PortView } from './api'
import type { Selection } from './portActions'

// Why a P3b command (Apply, Ping, Scan, Set as L2 destination) cannot be sent now, or null. The order is the order the user can fix them in.
export const WRITABLE = ['IDLE', 'STREAMS', 'ASTF_LOADED']   // the server's own rule for configuration (common/trex_port.py:176-178)
export const BUSY_WORD: Record<BusyKind, string> = { ping: 'pinging', scan: 'scanning', apply: 'applying' }

export function p3bBlock(a: { port: PortView; connected: boolean; readOnly: boolean; selection: Selection; busyKind: BusyKind | undefined }): string | null {
  const { port, connected, readOnly, selection, busyKind } = a
  const secondary = selection.kind === 'profile' && selection.profile !== '_'
  return !connected ? 'not connected' : readOnly ? 'read-only session' : secondary ? 'select the default profile' : !port.owned ? 'acquire the port first'
    : !WRITABLE.includes(port.state) ? 'transmitting: stop traffic first'
      : busyKind ? `busy: ${BUSY_WORD[busyKind]}`
        : port.service_filtered ? 'filtered service mode: set it on or off in the Control tab' : null
}

// A reason as a visible line beside the actions: "acquire the port first" becomes "Acquire the port first."
export const sentence = (why: string) => `${why[0].toUpperCase()}${why.slice(1)}.`

export const errText = (x: unknown) => (x instanceof Error ? x.message : String(x))
const IPV4 = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/
export const isIpv4 = (s: string) => { const m = IPV4.exec(s); return m !== null && m.slice(1).every((o) => Number(o) <= 255) }
export const isIpv6 = (s: string) => /^[0-9a-fA-F:.]+$/.test(s) && s.split(':').length >= 3
