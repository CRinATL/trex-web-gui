import { describe, expect, it } from 'vitest'
import type { PortIface } from '../api'
import { ifaceText } from './ifaceText'

const iface = (o: Partial<PortIface> = {}): PortIface => ({ port: 0, cfg_entry: '03:00.0', kind: 'pci', kernel_name: null, pci_addr: '0000:03:00.0',
  driver: 'vfio-pci', vendor_device: '8086:1572', model: 'Intel X710', confirmed: true, note: 'no kernel interface: bound to vfio-pci', ...o })

describe('ifaceText title', () => {
  it('carries the note and the model', () => {
    expect(ifaceText(iface()).title).toBe('no kernel interface: bound to vfio-pci\nIntel X710')
  })
  it('falls back to the raw ids when lspci gave no model', () => {
    expect(ifaceText(iface({ model: null })).title).toBe('no kernel interface: bound to vfio-pci\n8086:1572')
  })
})
