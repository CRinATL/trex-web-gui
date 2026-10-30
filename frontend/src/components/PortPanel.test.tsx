import { act, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import type { PortIface, PortView, ServerInfo } from '../api'
import { wireProfiles } from '../testWire'
import { PortPanel } from './PortPanel'

const port = (id: number, o: Partial<PortView> = {}): PortView => ({
  id, state: 'IDLE', owner: 'me', owned: true, service: false, service_filtered: false, link_up: true, speed_gbps: 10, promiscuous: false,
  multicast: false, fc_mode: -1, vlan: [], layer_cfg: {}, rx_filter_mode: null, profiles: wireProfiles(o.state), rx_info: {}, info: { driver: 'net_af_packet' }, ...o,
})
// index order is 1 then 0, as the measured server reports it
const info: ServerInfo = { version: 'v3.08', mode: 'STL', build_date: 'Oct 1', hostname: 'trex-vm', port_count: 2, core_type: 'Xeon',
  ports: [{ index: 1, driver: 'drv1', description: 'Unknown', hw_mac: 'aa:aa', numa: -1, pci_addr: 'N/A' }, { index: 0, driver: 'drv0', description: 'Unknown', hw_mac: 'bb:bb', numa: 0, pci_addr: '0000:01:00.0' }] }

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers() })

const iface = (port: number, o: Partial<PortIface> = {}): PortIface => ({ port, cfg_entry: null, kind: null, kernel_name: null, pci_addr: null, driver: null,
  vendor_device: null, model: null, confirmed: false, note: null, ...o })

describe('PortPanel', () => {
  it('adds an Interface column: the kernel name when confirmed, else the cfg entry muted and marked unconfirmed', () => {
    const ifaces = [iface(0, { cfg_entry: 'veth0', kernel_name: 'veth0', confirmed: true, kind: 'af_packet', model: 'Intel X710' }),
      iface(1, { cfg_entry: 'veth1', kind: 'af_packet', note: 'veth1 not confirmed: no such interface with MAC aa:aa here' })]
    render(<PortPanel selection={{ kind: 'root' }} ports={[port(0), port(1)]} info={info} readOnly={false} connected ifaces={ifaces} ifacesNote="the config is fine" />)
    expect(screen.getByRole('columnheader', { name: 'Interface' })).toBeInTheDocument()
    const rows = screen.getAllByRole('row').slice(-2)
    const c0 = within(rows[0]).getByText('veth0')
    expect(c0).not.toHaveClass('muted')
    expect(c0).toHaveAttribute('title', expect.stringContaining('Intel X710'))
    expect(rows[0].textContent).not.toContain('(unconfirmed)')
    const c1 = within(rows[1]).getByText(/veth1/)
    expect(c1).toHaveClass('muted')
    expect(c1.textContent).toContain('(unconfirmed)')
    expect(c1).toHaveAttribute('title', expect.stringContaining('not confirmed'))
    expect(screen.getByText('the config is fine')).toBeInTheDocument()
    // identifiers in fixed width (RC1-15): MAC, PCI and Interface
    for (const text of ['bb:bb', '0000:01:00.0', 'veth0']) expect(within(rows[0]).getByText(text)).toHaveClass('mono')
    expect(within(rows[0]).getByText('drv0')).not.toHaveClass('mono')
  })
  it('shows a dash in the Interface column for a port with nothing known', () => {
    render(<PortPanel selection={{ kind: 'root' }} ports={[port(0), port(1)]} info={info} readOnly={false} connected ifaces={[iface(0)]} />)
    const rows = screen.getAllByRole('row').slice(-2)
    expect(within(rows[0]).getAllByRole('cell')[6]).toHaveTextContent('–')
    expect(within(rows[1]).getAllByRole('cell')[6]).toHaveTextContent('–')
  })
  it('shows the system-info pane for the root, with each port in index order', () => {
    render(<PortPanel selection={{ kind: 'root' }} ports={[port(0), port(1)]} info={info} readOnly={false} connected />)
    expect(screen.getByText(/v3\.08/)).toBeInTheDocument()
    const rows = screen.getAllByRole('row').slice(-2).map((r) => r.textContent)
    expect(rows[0]).toContain('drv0')
    expect(rows[0]).toContain('bb:bb')
    expect(rows[0]).toContain('0000:01:00.0')
    expect(rows[1]).toContain('drv1')
    expect(screen.queryByRole('tablist')).toBeNull()
  })
  it('shows Control, Config, Traffic, Counters and Tools tabs for a port, switchable with the arrow keys', async () => {
    render(<PortPanel selection={{ kind: 'port', id: 0 }} ports={[port(0)]} info={info} readOnly={false} connected />)
    const tab = (n: string) => screen.getByRole('tab', { name: n })
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual(['Control', 'Config', 'Traffic', 'Counters', 'Tools'])
    expect(tab('Control')).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('button', { name: 'Reset…' })).toBeInTheDocument()
    tab('Control').focus()
    await userEvent.keyboard('{ArrowRight}')
    expect(tab('Config')).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('button', { name: 'Apply' })).toBeInTheDocument()
    await userEvent.keyboard('{ArrowRight}')
    expect(tab('Traffic')).toHaveAttribute('aria-selected', 'true')
    expect(tab('Traffic')).toHaveFocus()
    expect(screen.getByRole('button', { name: 'Inspect' })).toBeInTheDocument()
    await userEvent.keyboard('{ArrowRight}')
    expect(tab('Counters')).toHaveAttribute('aria-selected', 'true')
    await userEvent.keyboard('{ArrowRight}')
    expect(tab('Tools')).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'Ping' })).toBeInTheDocument()
    await userEvent.keyboard('{ArrowRight}')
    expect(tab('Control')).toHaveAttribute('aria-selected', 'true')
    await userEvent.keyboard('{ArrowLeft}')
    expect(tab('Tools')).toHaveAttribute('aria-selected', 'true')
  })
  it('keeps a Ping destination typed in Tools across a tab switch', async () => {
    render(<PortPanel selection={{ kind: 'port', id: 0 }} ports={[port(0)]} info={info} readOnly={false} connected />)
    await userEvent.click(screen.getByRole('tab', { name: 'Tools' }))
    await userEvent.type(screen.getByLabelText('Destination'), '10.1.1.1')
    await userEvent.click(screen.getByRole('tab', { name: 'Config' }))
    await userEvent.click(screen.getByRole('tab', { name: 'Tools' }))
    expect(screen.getByLabelText('Destination')).toHaveValue('10.1.1.1')
  })
  it('opens on Traffic and focuses the requested control when asked, once per request', () => {
    const p = [port(0, { state: 'STREAMS' })]
    const { rerender } = render(<PortPanel selection={{ kind: 'port', id: 0 }} ports={p} info={info} readOnly={false} connected />)
    expect(screen.getByRole('tab', { name: 'Control' })).toHaveAttribute('aria-selected', 'true')
    rerender(<PortPanel selection={{ kind: 'port', id: 0 }} ports={p} info={info} readOnly={false} connected request={{ port: 0, target: 'mult', seq: 1 }} />)
    expect(screen.getByRole('tab', { name: 'Traffic' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByLabelText('Multiplier')).toHaveFocus()
  })
  it('a request present at mount is not replayed', () => {
    render(<PortPanel selection={{ kind: 'port', id: 0 }} ports={[port(0)]} info={info} readOnly={false} connected request={{ port: 0, target: 'mult', seq: 4 }} />)
    expect(screen.getByRole('tab', { name: 'Control' })).toHaveAttribute('aria-selected', 'true')
  })
  it('keeps the Traffic tab mounted while Control is shown, and aria-controls always points at a rendered panel', async () => {
    const { container } = render(<PortPanel selection={{ kind: 'port', id: 0 }} ports={[port(0)]} info={info} readOnly={false} connected />)
    await userEvent.click(screen.getByRole('tab', { name: 'Traffic' }))
    await userEvent.type(screen.getByRole('textbox', { name: /Profile path/ }), '/p/a.py')
    await userEvent.click(screen.getByRole('tab', { name: 'Control' }))
    expect(screen.queryByRole('textbox', { name: /Profile path/ })).toBeNull()   // hidden
    await userEvent.click(screen.getByRole('tab', { name: 'Traffic' }))
    expect(screen.getByRole('textbox', { name: /Profile path/ })).toHaveValue('/p/a.py')
    for (const t of screen.getAllByRole('tab')) expect(container.querySelector(`#${t.getAttribute('aria-controls')}`)).not.toBeNull()
  })
  it('switching to the Control tab cancels the countdown', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false })
    const up = vi.spyOn(api, 'update').mockResolvedValue({ ports: [] })
    const p = [port(0, { state: 'TX' })]
    render(<PortPanel selection={{ kind: 'port', id: 0 }} ports={p} info={info} readOnly={false} connected />)
    fireEvent.click(screen.getByRole('tab', { name: 'Traffic' }))
    fireEvent.change(screen.getByLabelText('Multiplier'), { target: { value: '20' } })
    fireEvent.keyDown(screen.getByLabelText('Multiplier'), { key: 'Enter' })
    expect(screen.getByRole('status')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: 'Control' }))
    for (let i = 0; i < 11; i++) await act(async () => { vi.advanceTimersByTime(1000) })
    expect(up).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('tab', { name: 'Traffic' }))
    expect(screen.getByLabelText('Multiplier')).toHaveValue('×1')
  })
})
