import type { PortIface } from '../api'

// How a port's interface reads in the tables: the confirmed kernel name, else the cfg entry marked unconfirmed (muted).
// `title` carries the note and the card model (the raw ids when lspci gave no name).
export function ifaceText(i: PortIface | undefined): { text: string; muted: boolean; title: string | undefined } {
  if (!i) return { text: '–', muted: false, title: undefined }
  const title = [i.note, i.model ?? i.vendor_device].filter(Boolean).join('\n') || undefined
  if (i.kernel_name && i.confirmed) return { text: i.kernel_name, muted: false, title }
  if (i.cfg_entry) return { text: `${i.cfg_entry} (unconfirmed)`, muted: true, title }
  return { text: '–', muted: false, title }
}

// The name offered when naming a port: only a confirmed kernel name, never the unconfirmed cfg entry.
export function confirmedName(ifaces: PortIface[], port: number): string | null {
  const i = ifaces.find((x) => x.port === port)
  return i && i.confirmed && i.kernel_name ? i.kernel_name : null
}
