import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import * as state from '../state'
import { wireProfiles } from '../testWire'
import { PortControlTab } from './PortControlTab'

// info mirrors the measured net_af_packet port (tests/fixtures/rpc.jsonl get_system_info)
const info = { driver: 'net_af_packet', description: 'Unknown', hw_mac: '8e:4e:97:11:bf:a8', pci_addr: 'N/A', numa: -1,
  is_prom_supported: true, is_led_supported: false, is_link_supported: false, is_fc_supported: false }
const port = (o: Partial<api.PortView> = {}): api.PortView => ({
  id: 0, state: 'IDLE', owner: 'me', owned: true, service: false, service_filtered: false, link_up: true, speed_gbps: 10, promiscuous: false,
  multicast: false, fc_mode: -1, vlan: [], layer_cfg: {}, rx_filter_mode: 'hw', profiles: wireProfiles(o.state), info,
  rx_info: { capture_port: { is_active: true }, grat_arp: { is_active: false }, queue: { is_active: false } }, ...o,
})
afterEach(() => vi.restoreAllMocks())
// the Port details rows sit in a closed fold; open it before reading a row
const openDetails = () => { screen.getByText(/^Port details/).closest('details')!.open = true }
const iface = (o: Partial<api.PortIface> = {}): api.PortIface => ({ port: 0, cfg_entry: '03:00.0', kind: 'pci', kernel_name: 'enp3s0f0', pci_addr: '0000:03:00.0',
  driver: 'ixgbe', vendor_device: '8086:10fb', model: 'Intel 82599ES', confirmed: true, note: null, ...o })

describe('PortControlTab', () => {
  it('shows Speed, Link, Owner and State as chips with their values', () => {
    render(<PortControlTab port={port({ state: 'TX', speed_gbps: 25, owner: 'bob' })} readOnly={false} />)
    const chips = within(screen.getByLabelText('Port status'))
    expect(chips.getByText('Speed 25 Gbps')).toHaveClass('chip')
    expect(chips.getByText('Link up')).toHaveClass('chip', 'ok')
    expect(chips.getByText('Owner bob')).toHaveClass('chip')
    expect(chips.getByText('State TX')).toHaveClass('chip', 'st-TX')
  })
  it('a link that is down is a bad chip, and an unknown one is plain', () => {
    const { unmount } = render(<PortControlTab port={port({ link_up: false })} readOnly={false} />)
    expect(screen.getByText('Link down')).toHaveClass('chip', 'bad')
    unmount()
    render(<PortControlTab port={port({ link_up: null, speed_gbps: null })} readOnly={false} />)
    expect(screen.getByText('Link –')).not.toHaveClass('ok', 'bad')
    expect(screen.getByText('Speed –')).toBeInTheDocument()
  })
  it('the other rows are folded in Port details, closed by default', () => {
    render(<PortControlTab port={port()} readOnly={false} />)
    const fold = screen.getByText('Port details (13)').closest('details')!
    expect(fold).toHaveClass('fold')
    expect(fold.open).toBe(false)
    const th = [...fold.querySelectorAll('th')].map((e) => e.textContent)
    expect(th).toEqual(['Driver', 'Description', 'MAC', 'PCI', 'Interface', 'Card', 'Bound driver', 'NUMA', 'RX filter mode', 'VLAN tags', 'Capture port', 'Grat ARP', 'RX queueing'])
    // the chips and the controls are not repeated as rows
    for (const k of ['Speed', 'Link', 'Owner', 'State', 'Service', 'Promiscuous', 'Multicast', 'Flow control']) expect(within(fold).queryByText(k, { selector: 'th' })).toBeNull()
  })
  it('LED is in the settings block', () => {
    render(<PortControlTab port={port()} readOnly={false} />)
    const block = within(screen.getByRole('group', { name: 'Settings' }))
    for (const n of ['LED', 'Promiscuous', 'Multicast', 'Link up', 'Flow control', 'Off', 'On', 'Filtered']) expect(block.getByLabelText(n)).toBeInTheDocument()
    expect(block.getByRole('textbox', { name: /^Name/ })).toBeInTheDocument()
  })
  it('shows Interface, Card and Bound driver beside the PCI row, and the note as muted text', () => {
    render(<PortControlTab port={port()} readOnly={false} iface={iface({ note: 'no kernel interface: bound to vfio-pci', kernel_name: null })} />)
    openDetails()
    const cellOf = (k: string) => screen.getByText(k, { selector: 'th' }).nextElementSibling?.textContent
    expect(cellOf('Interface')).toBe('03:00.0 (unconfirmed)')
    expect(screen.getByText('Interface', { selector: 'th' }).nextElementSibling).toHaveClass('muted')
    expect(cellOf('Card')).toBe('Intel 82599ES')
    expect(cellOf('Bound driver')).toBe('ixgbe')
    expect(screen.getByText('no kernel interface: bound to vfio-pci')).toHaveClass('muted')
    expect(screen.getByText('no kernel interface: bound to vfio-pci').closest('details')).toHaveClass('fold')   // under the table, inside the fold
  })
  it('falls back to the raw vendor:device ids on the Card row when lspci gave no model', () => {
    render(<PortControlTab port={port()} readOnly={false} iface={iface({ model: null, vendor_device: '8086:1572' })} />)
    openDetails()
    expect(screen.getByText('Card', { selector: 'th' }).nextElementSibling?.textContent).toBe('8086:1572')
  })
  it('shows the confirmed kernel name plainly, and dashes when nothing is known', () => {
    const { unmount } = render(<PortControlTab port={port()} readOnly={false} iface={iface()} />)
    openDetails()
    expect(screen.getByText('Interface', { selector: 'th' }).nextElementSibling?.textContent).toBe('enp3s0f0')
    unmount()
    render(<PortControlTab port={port()} readOnly={false} />)
    openDetails()
    expect(screen.getByText('Interface', { selector: 'th' }).nextElementSibling?.textContent).toBe('–')
    expect(screen.getByText('Card', { selector: 'th' }).nextElementSibling?.textContent).toBe('–')
    expect(screen.getByText('Bound driver', { selector: 'th' }).nextElementSibling?.textContent).toBe('–')
  })
  it('lists the attributes', () => {
    render(<PortControlTab port={port()} readOnly={false} />)
    openDetails()
    const cellOf = (k: string) => screen.getByText(k, { selector: 'th' }).nextElementSibling?.textContent
    expect(cellOf('Driver')).toBe('net_af_packet')
    expect(cellOf('MAC')).toBe('8e:4e:97:11:bf:a8')
    expect(cellOf('PCI')).toBe('N/A')
    expect(cellOf('NUMA')).toBe('-1')
    expect(cellOf('Capture port')).toBe('yes')
    expect(cellOf('Grat ARP')).toBe('no')
    expect(cellOf('RX queueing')).toBe('no')
    expect(cellOf('RX filter mode')).toBe('hw')
  })
  it('gates each toggle on support: promiscuous enabled, LED, link and flow control disabled with the reason', () => {
    render(<PortControlTab port={port()} readOnly={false} />)
    expect(screen.getByLabelText('Promiscuous')).toBeEnabled()
    expect(screen.getByLabelText('LED')).toBeDisabled()
    expect(screen.getByLabelText('LED')).toHaveAttribute('title', 'not supported by net_af_packet')
    expect(screen.getByLabelText('Link up')).toBeDisabled()
    expect(screen.getByLabelText('Flow control')).toBeDisabled()
    expect(screen.getByLabelText('Multicast')).toBeEnabled()
  })
  it('disables every toggle when the port is not owned, saying to acquire it', () => {
    render(<PortControlTab port={port({ owned: false, owner: 'bob' })} readOnly={false} />)
    for (const n of ['Promiscuous', 'Multicast', 'LED', 'Link up', 'Flow control', 'Off', 'On', 'Filtered']) {
      expect(screen.getByLabelText(n)).toBeDisabled()
    }
    expect(screen.getByLabelText('Promiscuous')).toHaveAttribute('title', 'acquire the port first')
  })
  it('disables every toggle in a read-only session', () => {
    render(<PortControlTab port={port()} readOnly />)
    expect(screen.getByLabelText('Promiscuous')).toBeDisabled()
    expect(screen.getByLabelText('Promiscuous')).toHaveAttribute('title', 'read-only session')
    expect(screen.getByRole('button', { name: 'Reset…' })).toBeDisabled()
  })
  it('a toggle calls setAttr with the right body', async () => {
    const set = vi.spyOn(api, 'setAttr').mockResolvedValue({ ports: [] })
    render(<PortControlTab port={port()} readOnly={false} />)
    await userEvent.click(screen.getByLabelText('Promiscuous'))
    expect(set).toHaveBeenLastCalledWith({ ports: [0], promiscuous: true })
    await userEvent.click(screen.getByLabelText('Multicast'))
    expect(set).toHaveBeenLastCalledWith({ ports: [0], multicast: true })
  })
  it('flow control, LED and link send their bodies when supported', async () => {
    const set = vi.spyOn(api, 'setAttr').mockResolvedValue({ ports: [] })
    render(<PortControlTab port={port({ fc_mode: 0, info: { ...info, is_fc_supported: true, is_led_supported: true, is_link_supported: true } })} readOnly={false} />)
    await userEvent.selectOptions(screen.getByLabelText('Flow control'), 'FULL')
    expect(set).toHaveBeenLastCalledWith({ ports: [0], flow_ctrl: 3 })
    await userEvent.click(screen.getByLabelText('LED'))
    expect(set).toHaveBeenLastCalledWith({ ports: [0], led_on: true })
    await userEvent.click(screen.getByLabelText('Link up'))
    expect(set).toHaveBeenLastCalledWith({ ports: [0], link_up: false })
  })
  it('service mode is Off / On / Filtered; On and Off apply at once with the right bodies', async () => {
    const svc = vi.spyOn(api, 'setService').mockResolvedValue({ ports: [] })
    const { rerender } = render(<PortControlTab port={port()} readOnly={false} />)
    expect(screen.getByLabelText('Off')).toBeChecked()
    await userEvent.click(screen.getByLabelText('On'))
    expect(svc).toHaveBeenLastCalledWith({ ports: [0], enabled: true })
    rerender(<PortControlTab port={port({ service: true })} readOnly={false} />)
    await userEvent.click(screen.getByLabelText('Off'))
    expect(svc).toHaveBeenLastCalledWith({ ports: [0], enabled: false })
  })
  it('Filtered shows the masks, defaults to No TCP/UDP and sends enabled false with the mask', async () => {
    const svc = vi.spyOn(api, 'setService').mockResolvedValue({ ports: [] })
    render(<PortControlTab port={port()} readOnly={false} />)
    expect(screen.queryByLabelText('BGP')).toBeNull()
    expect(screen.getByText('Filtered mode needs the TRex server started with --software.')).toBeInTheDocument()
    await userEvent.click(screen.getByLabelText('Filtered'))
    expect(svc).not.toHaveBeenCalled()   // choosing Filtered only opens the masks
    expect(screen.getByLabelText('No TCP/UDP')).toBeChecked()
    expect(screen.getByLabelText('BGP')).not.toBeChecked()
    await userEvent.click(screen.getByRole('button', { name: 'Apply filter' }))
    expect(svc).toHaveBeenLastCalledWith({ ports: [0], enabled: false, filtered: true, mask: 1 })
    await userEvent.click(screen.getByLabelText('BGP'))
    await userEvent.click(screen.getByLabelText('mDNS'))
    await userEvent.click(screen.getByRole('button', { name: 'Apply filter' }))
    expect(svc).toHaveBeenLastCalledWith({ ports: [0], enabled: false, filtered: true, mask: 19 })
    await userEvent.click(screen.getByLabelText('All'))
    await userEvent.click(screen.getByRole('button', { name: 'Apply filter' }))
    expect(svc).toHaveBeenLastCalledWith({ ports: [0], enabled: false, filtered: true, mask: 255 })
  })
  it('an empty mask cannot be applied', async () => {
    const svc = vi.spyOn(api, 'setService').mockResolvedValue({ ports: [] })
    render(<PortControlTab port={port()} readOnly={false} />)
    await userEvent.click(screen.getByLabelText('Filtered'))
    await userEvent.click(screen.getByLabelText('No TCP/UDP'))
    expect(screen.getByRole('button', { name: 'Apply filter' })).toBeDisabled()
    expect(svc).not.toHaveBeenCalled()
  })
  it('a filtered port shows Filtered selected and "filtered" in the table', () => {
    render(<PortControlTab port={port({ service: false, service_filtered: true })} readOnly={false} />)
    // the Service row left the table: the control carries the mode
    expect(within(screen.getByRole('radiogroup', { name: 'Service mode' })).getByLabelText('Filtered')).toBeChecked()
  })
  it('link down on a transmitting port warns first and proceeds on the second click', async () => {
    const set = vi.spyOn(api, 'setAttr').mockResolvedValue({ ports: [] })
    render(<PortControlTab port={port({ state: 'TX', info: { ...info, is_link_supported: true } })} readOnly={false} />)
    await userEvent.click(screen.getByLabelText('Link up'))
    expect(set).not.toHaveBeenCalled()
    expect(screen.getByRole('status')).toHaveTextContent('Link down while transmitting can hang the TRex v3.08 server (upstream issue #1207). Stop traffic first, or click again to proceed.')
    await userEvent.click(screen.getByLabelText('Link up'))
    expect(set).toHaveBeenCalledWith({ ports: [0], link_up: false })
    expect(screen.queryByRole('status')).toBeNull()
  })
  it('link down on a PCAP_TX port asks for confirmation first', async () => {
    const set = vi.spyOn(api, 'setAttr').mockResolvedValue({ ports: [] })
    render(<PortControlTab port={port({ state: 'PCAP_TX', info: { ...info, is_link_supported: true } })} readOnly={false} />)
    await userEvent.click(screen.getByLabelText('Link up'))
    expect(set).not.toHaveBeenCalled()
    expect(screen.getByRole('status')).toHaveTextContent('Link down while transmitting')
  })
  it('link down on a paused port also warns first', async () => {
    const set = vi.spyOn(api, 'setAttr').mockResolvedValue({ ports: [] })
    render(<PortControlTab port={port({ state: 'PAUSE', info: { ...info, is_link_supported: true } })} readOnly={false} />)
    await userEvent.click(screen.getByLabelText('Link up'))
    expect(set).not.toHaveBeenCalled()
    expect(screen.getByRole('status')).toBeInTheDocument()
  })
  it('a rejected On puts the radio back to the server state and shows the error', async () => {
    vi.spyOn(api, 'setService').mockRejectedValue(new api.ApiError(409, 'x', 'service refused'))
    render(<PortControlTab port={port()} readOnly={false} />)
    await userEvent.click(screen.getByLabelText('On'))
    expect(await screen.findByRole('alert')).toHaveTextContent('service refused')
    expect(screen.getByLabelText('Off')).toBeChecked()
    expect(screen.getByLabelText('On')).not.toBeChecked()
  })
  it('unticking a bit while All is on sends only the five named bits', async () => {
    const svc = vi.spyOn(api, 'setService').mockResolvedValue({ ports: [] })
    render(<PortControlTab port={port()} readOnly={false} />)
    await userEvent.click(screen.getByLabelText('Filtered'))
    await userEvent.click(screen.getByLabelText('All'))
    await userEvent.click(screen.getByLabelText('BGP'))
    await userEvent.click(screen.getByRole('button', { name: 'Apply filter' }))
    expect(svc).toHaveBeenLastCalledWith({ ports: [0], enabled: false, filtered: true, mask: 29 })
  })
  it('link down on an idle port needs no confirm', async () => {
    const set = vi.spyOn(api, 'setAttr').mockResolvedValue({ ports: [] })
    render(<PortControlTab port={port({ state: 'IDLE', info: { ...info, is_link_supported: true } })} readOnly={false} />)
    await userEvent.click(screen.getByLabelText('Link up'))
    expect(set).toHaveBeenCalledWith({ ports: [0], link_up: false })
  })
  it('a rejection shows the backend message', async () => {
    vi.spyOn(api, 'setAttr').mockRejectedValue(new api.ApiError(409, 'x', 'port 0 is not owned'))
    render(<PortControlTab port={port()} readOnly={false} />)
    await userEvent.click(screen.getByLabelText('Promiscuous'))
    expect(await screen.findByRole('alert')).toHaveTextContent('port 0 is not owned')
  })
  it('Reset needs two clicks and says what it does', async () => {
    const reset = vi.spyOn(api, 'resetPorts').mockResolvedValue({ ports: [] })
    render(<PortControlTab port={port()} readOnly={false} />)
    expect(screen.getByText(/takes the port from any owner, stops traffic and removes all streams/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Reset…' }))
    expect(reset).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Click again to confirm reset' }))
    expect(reset).toHaveBeenCalledWith({ ports: [0] })
  })
  it('Reset confirmation lists the server captures and unsaved packets', async () => {
    const cap = (id: number, count: number) => ({ id, count } as api.CaptureRow)
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, capture: { ...state.initialState.capture, captures: [cap(1, 120), cap(2, 0)] } })
    const reset = vi.spyOn(api, 'resetPorts').mockResolvedValue({ ports: [] })
    render(<PortControlTab port={port()} readOnly={false} />)
    await userEvent.click(screen.getByRole('button', { name: 'Reset…' }))
    expect(screen.getByRole('status')).toHaveTextContent('Captures 1, 2 will be removed from the server (120 unsaved packets)')
    expect(reset).not.toHaveBeenCalled()
  })
  it('Reset confirmation says nothing about captures when the server lists none', async () => {
    render(<PortControlTab port={port()} readOnly={false} />)
    await userEvent.click(screen.getByRole('button', { name: 'Reset…' }))
    expect(screen.queryByRole('status')).toBeNull()
  })
  it('re-syncs the service mode when the server reports a change', () => {
    const { rerender } = render(<PortControlTab port={port()} readOnly={false} />)
    expect(screen.getByLabelText('Filtered')).not.toBeChecked()
    rerender(<PortControlTab port={port({ service: false, service_filtered: true })} readOnly={false} />)
    expect(screen.getByLabelText('Filtered')).toBeChecked()
  })
  const GATED = ['Promiscuous', 'Multicast', 'Off', 'On', 'Filtered']
  it('controls are disabled when not connected', () => {
    render(<PortControlTab port={port()} readOnly={false} connected={false} />)
    for (const n of GATED) expect(screen.getByLabelText(n)).toBeDisabled()
    expect(screen.getByLabelText('Promiscuous')).toHaveAttribute('title', 'not connected')
    expect(screen.getByRole('button', { name: 'Reset…' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Reset…' })).toHaveAttribute('title', 'not connected')
  })
  it('controls are disabled for a secondary profile', () => {
    render(<PortControlTab port={port()} readOnly={false} selection={{ kind: 'profile', port: 0, profile: 'b' }} />)
    for (const n of GATED) expect(screen.getByLabelText(n)).toBeDisabled()
    expect(screen.getByLabelText('Promiscuous')).toHaveAttribute('title', 'select the default profile')
    expect(screen.getByRole('button', { name: 'Reset…' })).toBeDisabled()
  })
  it('Apply filter obeys the connected and selection gate', () => {
    const p = port({ service_filtered: true })
    const { rerender } = render(<PortControlTab port={p} readOnly={false} connected={false} />)
    expect(screen.getByRole('button', { name: 'Apply filter' })).toBeDisabled()
    rerender(<PortControlTab port={p} readOnly={false} selection={{ kind: 'profile', port: 0, profile: 'b' }} />)
    expect(screen.getByRole('button', { name: 'Apply filter' })).toBeDisabled()
  })
  it('the default profile selection leaves the controls enabled', () => {
    render(<PortControlTab port={port()} readOnly={false} selection={{ kind: 'profile', port: 0, profile: '_' }} />)
    expect(screen.getByLabelText('Promiscuous')).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Reset…' })).toBeEnabled()
  })
})
