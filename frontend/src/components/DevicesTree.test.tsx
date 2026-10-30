import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppStateProvider } from '../state'
import { usePortDisplayLabelOf, usePortLabelOf } from '../portLabels'
import * as state from '../state'
import type * as api from '../api'
import * as ws from '../ws'
import type { PortView } from '../api'
import type { Selection } from '../portActions'
import { wirePort, wireProfiles } from '../testWire'
import { DevicesTree, INFERRED_HINT } from './DevicesTree'

const port = (id: number, o: Partial<PortView> = {}): PortView => ({
  id, state: 'IDLE', owner: '', owned: false, service: false, service_filtered: false, link_up: true, speed_gbps: 10,
  promiscuous: false, multicast: false, fc_mode: 0, vlan: [], layer_cfg: {}, rx_filter_mode: null, profiles: wireProfiles(o.state), rx_info: {}, info: {}, ...o,
})
const ports = [port(0, { state: 'TX', owned: true, owner: 'me', service: true }), port(1, { state: 'STREAMS', owner: 'bob', profiles: { p2: 'STREAMS' } })]

function setup(selection: Selection = { kind: 'root' }) {
  const onSelect = vi.fn(), onContextMenu = vi.fn()
  render(<DevicesTree host="10.0.0.1" ports={ports} selection={selection} onSelect={onSelect} onContextMenu={onContextMenu} />)
  return { onSelect, onContextMenu }
}

afterEach(() => vi.restoreAllMocks())

describe('DevicesTree', () => {
  it('shows the SVC badge for filtered service mode', () => {
    const f = [port(0, { owned: true, owner: 'me', service_filtered: true }), port(1, { owned: true, owner: 'me', service: true })]
    render(<DevicesTree host="h" ports={f} selection={{ kind: 'root' }} onSelect={vi.fn()} onContextMenu={vi.fn()} />)
    expect(screen.getAllByText('SVC')).toHaveLength(2)
    expect(screen.getByRole('treeitem', { name: /^Port 0,.*service mode filtered$/ })).toBeInTheDocument()
    expect(screen.getByRole('treeitem', { name: /^Port 1,.*service mode on$/ })).toBeInTheDocument()
  })
  it('labels the root, ports and profiles, with the SVC badge only on a service port', () => {
    setup()
    expect(screen.getByRole('tree')).toBeInTheDocument()
    expect(screen.getByRole('treeitem', { name: /TRex-10\.0\.0\.1/ })).toBeInTheDocument()
    expect(screen.getByRole('treeitem', { name: 'Port 0, TX, owned by you, service mode on' })).toBeInTheDocument()
    expect(screen.getByRole('treeitem', { name: 'Port 1, STREAMS, owned by bob' })).toBeInTheDocument()
    expect(screen.getAllByText('SVC')).toHaveLength(1)
    expect(screen.getByText('TX')).toHaveClass('st-TX')
    expect(screen.getByRole('treeitem', { name: /Profile _ \(TX\)/ })).toBeInTheDocument()
    expect(screen.getByRole('treeitem', { name: /Profile p2 \(STREAMS\).*read-only/ })).toBeInTheDocument()
  })
  it('port rows with profile children are aria-expanded', () => {
    setup()
    expect(screen.getByRole('treeitem', { name: /^Port 1,/ })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('treeitem', { name: /TRex-10/ })).not.toHaveAttribute('aria-expanded')
  })
  it('shows an em dash for an unowned port', () => {
    render(<DevicesTree host="h" ports={[port(3)]} selection={{ kind: 'root' }} onSelect={() => undefined} onContextMenu={() => undefined} />)
    expect(screen.getByRole('treeitem', { name: 'Port 3, IDLE, not owned' })).toHaveTextContent('—')
  })
  it('ArrowDown and ArrowUp move the selection, Enter selects', async () => {
    const { onSelect } = setup()
    screen.getByRole('treeitem', { name: /TRex-10/ }).focus()
    await userEvent.keyboard('{ArrowDown}')
    expect(onSelect).toHaveBeenLastCalledWith({ kind: 'port', id: 0 })
    await userEvent.keyboard('{ArrowDown}{ArrowDown}')
    expect(onSelect).toHaveBeenLastCalledWith({ kind: 'port', id: 1 })
    await userEvent.keyboard('{ArrowUp}')
    expect(onSelect).toHaveBeenLastCalledWith({ kind: 'profile', port: 0, profile: '_' })
    onSelect.mockClear()
    await userEvent.keyboard('{Enter}')
    expect(onSelect).toHaveBeenCalledWith({ kind: 'profile', port: 0, profile: '_' })
  })
  it('Shift+F10 and the ContextMenu key open the menu for the focused row', async () => {
    const { onContextMenu } = setup()
    const row = screen.getByRole('treeitem', { name: /^Port 1,/ })
    row.focus()
    await userEvent.keyboard('{Shift>}{F10}{/Shift}')
    expect(onContextMenu).toHaveBeenLastCalledWith({ kind: 'port', id: 1 }, expect.any(Number), expect.any(Number))
    onContextMenu.mockClear()
    await userEvent.keyboard('{ContextMenu}')
    expect(onContextMenu).toHaveBeenCalledTimes(1)
  })
  it('right-click selects the row and opens the menu at the pointer', () => {
    const { onSelect, onContextMenu } = setup()
    fireEvent.contextMenu(screen.getByRole('treeitem', { name: /^Port 0,/ }), { clientX: 40, clientY: 50 })
    expect(onSelect).toHaveBeenCalledWith({ kind: 'port', id: 0 })
    expect(onContextMenu).toHaveBeenCalledWith({ kind: 'port', id: 0 }, 40, 50)
  })
  it('shows the name in the row and carries the description in the accessible name and the tooltip', () => {
    const labelOf = (p: PortView) => (p.id === 0 ? { name: 'uplink-A', description: 'to the DUT' } : null)
    render(<DevicesTree host="h" ports={ports} selection={{ kind: 'root' }} onSelect={vi.fn()} onContextMenu={vi.fn()} labelOf={labelOf} />)
    const row = screen.getByRole('treeitem', { name: 'Port 0 — uplink-A, to the DUT, TX, owned by you, service mode on' })
    expect(row).toHaveTextContent('Port 0 — uplink-A')
    expect(row).toHaveAttribute('title', 'to the DUT')
    expect(screen.getByRole('treeitem', { name: 'Port 1, STREAMS, owned by bob' })).not.toHaveAttribute('title')
  })
  it('a port_labels message re-renders the tree', () => {
    let onMsg: (m: unknown) => void = () => undefined
    vi.spyOn(ws, 'connectSocket').mockImplementation((_url, m) => { onMsg = m; return () => undefined })
    const params = { host: 'h', sync_port: 4501, async_port: 4500, timeout_s: 3 }
    const real = [wirePort('idle_owned', { id: 0 })]
    function Tree() {
      const labelOf = usePortLabelOf(real)
      return <DevicesTree host="h" ports={real} selection={{ kind: 'root' }} onSelect={vi.fn()} onContextMenu={vi.fn()} labelOf={labelOf} />
    }
    render(<AppStateProvider><Tree /></AppStateProvider>)
    act(() => onMsg({ type: 'session', state: 'connected', params, info: null, error: null, rpc_console: false, include_polling: false, connect_seq: 'p:1' }))
    expect(screen.getByRole('treeitem', { name: /^Port 0, / })).toBeInTheDocument()
    act(() => onMsg({ type: 'port_labels', labels: { 'h:4501': { '8e:4e:97:11:bf:a8': { name: 'uplink-A', description: '' } } }, load_error: null }))
    expect(screen.getByRole('treeitem', { name: /^Port 0 — uplink-A, / })).toBeInTheDocument()
    act(() => onMsg({ type: 'port_labels', labels: {}, load_error: null }))
    expect(screen.getByRole('treeitem', { name: /^Port 0, / })).toBeInTheDocument()
  })
  it('a port with no saved name shows its confirmed interface name in grey; a saved name wins', () => {
    const iface = (port: number, name: string): api.PortIface => ({ port, cfg_entry: '12:00.0', kind: 'pci', kernel_name: name, pci_addr: '0000:12:00.0',
      driver: 'mlx5_core', vendor_device: null, model: null, confirmed: true, note: null })
    const params = { host: '127.0.0.1', sync_port: 4501 } as api.ConnectParams
    const mac = (p: PortView, m: string) => ({ ...p, info: { hw_mac: m } })
    const two = [mac(port(0), '02:00:00:00:00:00'), mac(port(1), '02:00:00:00:00:01')]
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState,
      session: { ...state.initialState.session, state: 'connected', params, ifaces: [iface(0, 'ens1f0np0'), iface(1, 'ens1f1np1')] },
      portLabels: { '127.0.0.1:4501': { '02:00:00:00:00:01': { name: 'to the DUT', description: '' } } } })
    function Tree() {
      const labelOf = usePortDisplayLabelOf(two)
      return <DevicesTree host="h" ports={two} selection={{ kind: 'root' }} onSelect={vi.fn()} onContextMenu={vi.fn()} labelOf={labelOf} />
    }
    render(<Tree />)
    const grey = screen.getByText('ens1f0np0')
    expect(grey).toHaveClass('muted')
    expect(grey).toHaveClass('mono')     // an identifier, like the MAC and PCI columns (RC1-15)
    // port rows share the tree's columns, so each state badge starts at the same x whatever the name's width
    for (const row of screen.getAllByRole('treeitem').slice(1)) expect(row).toHaveClass('port-row')
    expect(screen.getByText('Port 1 — to the DUT')).toHaveClass('span-name')
    expect(grey.closest('[role="treeitem"]')).toHaveAttribute('title', INFERRED_HINT)
    expect(screen.getByText('Port 1 — to the DUT')).toBeInTheDocument()
    expect(screen.queryByText('ens1f1np1')).toBeNull()
  })
})
