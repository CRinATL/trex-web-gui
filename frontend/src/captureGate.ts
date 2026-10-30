import { setService, type PortView } from './api'

// Rx ports that are not in service mode, in the order asked. A port the server has not listed counts as off. Only Rx ports
// are gated: a Tx-only capture is not (the Capture page says what was measured about it).
export const serviceOffPorts = (rxPorts: number[], ports: PortView[]): number[] =>
  rxPorts.filter((id) => !ports.find((p) => p.id === id)?.service)

// Why a capture cannot start, or null. Connection and read-only come first: they override everything.
export function captureBlock({ connected, readOnly, monitorActive, rxPorts, ports }: {
  connected: boolean; readOnly: boolean; monitorActive: boolean; rxPorts: number[]; ports: PortView[]
}): string | null {
  if (!connected) return 'not connected'
  if (readOnly) return 'read-only session'
  if (monitorActive) return 'a monitor is already running'
  const off = serviceOffPorts(rxPorts, ports)
  return off.length ? `Rx port(s) ${off.join(', ')} are not in service mode` : null
}

// The limit every capture takes: the API's bound (422 beyond it), and the size above which the page warns.
export const MAX_LIMIT = 100_000
export const LARGE_LIMIT = 10_000
export const LARGE_LIMIT_NOTE = 'Large captures use about 9 KB of server memory per packet'
export const limitValid = (text: string): boolean => {
  const n = Number(text)
  return text.trim() !== '' && Number.isInteger(n) && n >= 1 && n <= MAX_LIMIT
}
// "Turn service mode on for ports ...": one set_service per port, in order. Capture never turns it off afterwards;
// turnServiceOff is the user's way back (the LLDP tab and the Dashboard bar, RC1-22/28).
export async function turnServiceOn(ports: number[]): Promise<void> {
  for (const p of ports) await setService({ ports: [p], enabled: true })
}
export async function turnServiceOff(ports: number[]): Promise<void> {
  for (const p of ports) await setService({ ports: [p], enabled: false })
}
