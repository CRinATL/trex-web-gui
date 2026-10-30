import { cleanup, render, screen, within as scoped } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import * as state from '../../state'
import captureMsg from '../../__fixtures__/captureMsg.json'
import lldpReply from '../../__fixtures__/lldpNeighbours.json'
import { LLDP_BPF, LLDP_NOTE, LLDP_RUNNING, LldpNeighbours, SERVICE_STAYS, START_HINT } from './LldpNeighbours'

// lldpNeighbours.json is the GET /api/capture/lldp body for two parsed frames (tests/unit/test_lldp.py writes and checks it).
const port = (id: number, service: boolean): api.PortView => ({ id, state: 'IDLE', owner: 'me', owned: true, service, service_filtered: false } as api.PortView)
const session = (o: Partial<api.SessionSnapshot> = {}): api.SessionSnapshot =>
  ({ type: 'session', state: 'connected', params: null, info: null, error: null, rpc_console: false, include_polling: false, ...o } as unknown as api.SessionSnapshot)
const running = captureMsg.monitor_and_recorder.monitor as api.MonitorState
const mockState = (o: Partial<state.AppState> = {}) => vi.spyOn(state, 'useAppState').mockReturnValue({
  ...state.initialState, session: session(), ports: [port(0, true), port(1, true)], ...o })
const reply = lldpReply as api.LldpReply
const lldpMonitor = (): Partial<state.AppState> => ({ capture: { ...state.initialState.capture, monitor: { ...running, bpf: LLDP_BPF } } })

beforeEach(() => { vi.spyOn(api, 'getLldp').mockResolvedValue(reply) })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('LldpNeighbours', () => {
  it('table from a fixture', async () => {
    mockState(lldpMonitor())
    render(<LldpNeighbours />)
    const table = await screen.findByRole('table', { name: 'LLDP neighbours' })
    const rows = scoped(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(2)
    const cells = (i: number) => scoped(rows[i]).getAllByRole('cell').map((c) => c.textContent)
    // Neighbour first (system name, then its port), the chassis after; a MAC's subtype is in the tooltip only.
    expect(cells(0)).toEqual(['0', '10', '', 'Gi1/0/2 (interface name)', '', '00:11:22:33:44:55', '', '', '120'])
    expect(cells(1)).toEqual(['1', '', 'sw1', 'Gi1/0/1 (interface name)', 'uplink to lab', '00:11:22:33:44:55',
      'bridge', '192.168.0.1', '120'])
    expect(scoped(rows[1]).getByText('sw1')).toHaveAttribute('title', 'Switch OS 1.0')
    expect(scoped(rows[1]).getByText('00:11:22:33:44:55').closest('td')).toHaveAttribute('title', 'Chassis ID subtype: MAC address')
    expect(scoped(rows[1]).getByText('Gi1/0/1').closest('td')).toHaveAttribute('title', 'Port ID subtype: interface name')
  })
  it('a MAC port ID shows no subtype, and each management address has its own line', async () => {
    const n = { ...reply.neighbours[1], port_id: '02:00:00:00:00:0a', port_id_subtype: 'MAC address',
      management_addresses: ['192.0.2.1', 'fe80::1'] }
    vi.mocked(api.getLldp).mockResolvedValue({ ...reply, neighbours: [n] })
    mockState(lldpMonitor())
    render(<LldpNeighbours />)
    const row = scoped(await screen.findByRole('table', { name: 'LLDP neighbours' })).getAllByRole('row')[1]
    const cells = scoped(row).getAllByRole('cell')
    expect(cells[3]).toHaveTextContent(/^02:00:00:00:00:0a$/)
    expect(cells[3]).toHaveAttribute('title', 'Port ID subtype: MAC address')
    expect([...cells[7].children].map((c) => c.textContent)).toEqual(['192.0.2.1', 'fe80::1'])
  })
  it('the firmware note is shown', async () => {
    mockState(lldpMonitor())
    render(<LldpNeighbours />)
    expect(await screen.findByText(reply.note)).toBeInTheDocument()
    expect(reply.note).toMatch(/firmware/)
  })
  it('the firmware note is shown before any monitor runs, and is the backend text', () => {
    mockState()
    render(<LldpNeighbours />)
    expect(api.getLldp).not.toHaveBeenCalled()
    expect(screen.getByText(LLDP_NOTE)).toBeInTheDocument()
    expect(LLDP_NOTE).toBe(reply.note)      // the fixture is the route body test_lldp.py checks against lldp.NOTE
  })
  it('Start sends the 0x88cc filter', async () => {
    mockState()
    const start = vi.spyOn(api, 'monitorStart').mockResolvedValue({ monitor: running })
    render(<LldpNeighbours />)
    const begin = screen.getByRole('button', { name: 'Start LLDP monitor' })
    expect(begin).toBeDisabled()
    expect(begin).toHaveAttribute('title', 'choose at least one Rx port')
    await userEvent.click(scoped(screen.getByRole('group', { name: 'LLDP Rx ports' })).getByRole('checkbox', { name: 'Port 1' }))
    await userEvent.click(begin)
    expect(start).toHaveBeenCalledWith({ tx: [], rx: [1], bpf: 'ether proto 0x88cc or (vlan and ether proto 0x88cc)', limit: 100 })
    expect(LLDP_BPF).toBe('ether proto 0x88cc or (vlan and ether proto 0x88cc)')
  })
  it('a running monitor disables Start with the reason', async () => {
    mockState({ capture: { ...state.initialState.capture, monitor: running } })
    render(<LldpNeighbours />)
    await userEvent.click(scoped(screen.getByRole('group', { name: 'LLDP Rx ports' })).getByRole('checkbox', { name: 'Port 1' }))
    const begin = screen.getByRole('button', { name: 'Start LLDP monitor' })
    expect(begin).toBeDisabled()
    expect(begin).toHaveAttribute('title', 'Stop the current monitor first')
    expect(screen.getByText('Stop the current monitor first')).toBeInTheDocument()
  })
  it('the running LLDP monitor is named as such, not as another monitor', async () => {
    mockState({ capture: { ...state.initialState.capture, monitor: { ...running, bpf: LLDP_BPF } } })
    render(<LldpNeighbours />)
    expect(screen.queryByRole('button', { name: 'Start LLDP monitor' })).toBeNull()
    expect(LLDP_RUNNING).toBe('LLDP monitor running')
    // Start and Stop are a pair (RC1-20): running, Start is lit with the state, Stop beside it
    expect(screen.getByRole('button', { name: LLDP_RUNNING })).toHaveAttribute('data-on')
    expect(screen.getByRole('button', { name: 'Stop LLDP monitor' })).toHaveAttribute('data-run', 'stop')
    expect(screen.queryByText('Stop the current monitor first')).toBeNull()
  })
  it('another monitor running: Start is blocked and unlit, Stop LLDP monitor disabled (review T2)', () => {
    mockState({ capture: { ...state.initialState.capture, monitor: { ...running, bpf: 'udp' } } })
    render(<LldpNeighbours />)
    const start = screen.getByRole('button', { name: 'Start LLDP monitor' })
    expect(start).toBeDisabled()
    expect(start).not.toHaveAttribute('data-on')
    expect(start).toHaveAttribute('title', 'Stop the current monitor first')
    expect(screen.getByRole('button', { name: 'Stop LLDP monitor' })).toHaveAttribute('title', 'the LLDP monitor is not running')
  })
  it('idle, Stop is there but disabled, saying why', () => {
    mockState()
    render(<LldpNeighbours />)
    const stop = screen.getByRole('button', { name: 'Stop LLDP monitor' })
    expect(stop).toBeDisabled()
    expect(stop).toHaveAttribute('title', 'the LLDP monitor is not running')
    expect(screen.getByRole('button', { name: 'Start LLDP monitor' })).not.toHaveAttribute('data-on')
  })
  it('the running LLDP monitor stops from this tab, and says service mode stays on', async () => {
    mockState({ ...lldpMonitor(), ports: [port(0, true), port(1, false)] })
    const stop = vi.spyOn(api, 'monitorStop').mockResolvedValue({ monitor: null } as never)
    render(<LldpNeighbours />)
    const rxOf = lldpMonitor().capture!.monitor!.rx
    expect(screen.getByLabelText('LLDP service mode')).toHaveTextContent(SERVICE_STAYS)
    expect(screen.getByLabelText('LLDP service mode')).toHaveTextContent(/^Service mode: /)
    expect(rxOf.length).toBeGreaterThan(0)
    await userEvent.click(screen.getByRole('button', { name: 'Stop LLDP monitor' }))
    expect(stop).toHaveBeenCalledTimes(1)
  })
  it('while running, the service line follows the monitor\'s Rx ports, not the picker; read-only Stop says why', () => {
    const mon = { ...state.initialState.capture, monitor: { ...running, bpf: LLDP_BPF, rx: [1] } }
    mockState({ capture: mon, ports: [port(0, true), port(1, false)], session: session({ params: { read_only: true } as never }) })
    render(<LldpNeighbours />)
    expect(screen.getByLabelText('LLDP service mode')).toHaveTextContent(/^Service mode: off for port 1\./)
    const stop = screen.getByRole('button', { name: 'Stop LLDP monitor' })
    expect(stop).toBeDisabled()
    expect(stop).toHaveAttribute('title', 'read-only session')
  })
  it('shows each chosen Rx port\'s service mode and offers to turn it on', async () => {
    mockState({ ports: [port(0, true), port(1, false)] })
    const set = vi.spyOn(api, 'setService').mockResolvedValue({} as never)
    render(<LldpNeighbours />)
    const group = scoped(screen.getByRole('group', { name: 'LLDP Rx ports' }))
    await userEvent.click(group.getByRole('checkbox', { name: 'Port 0' }))
    await userEvent.click(group.getByRole('checkbox', { name: 'Port 1' }))
    expect(screen.getByLabelText('LLDP service mode')).toHaveTextContent('Service mode: on for port 0; off for port 1.')
    await userEvent.click(screen.getByRole('button', { name: 'Turn service mode on for ports 1' }))
    expect(set).toHaveBeenCalledWith({ ports: [1], enabled: true })
  })
  it('stopped, turns service mode off on its owned Rx ports without leaving the page (RC1-28)', async () => {
    const theirs = { ...port(2, true), owner: 'bob', owned: false }
    mockState({ ports: [port(0, true), port(1, true), theirs] })
    const set = vi.spyOn(api, 'setService').mockResolvedValue({} as never)
    render(<LldpNeighbours />)
    const group = scoped(screen.getByRole('group', { name: 'LLDP Rx ports' }))
    for (const n of ['Port 0', 'Port 1', 'Port 2']) await userEvent.click(group.getByRole('checkbox', { name: n }))
    const off = screen.getByRole('button', { name: 'Turn service mode off for ports 0, 1' })   // port 2 is not ours
    expect(off).toHaveAttribute('data-run', 'go')
    await userEvent.click(off)
    expect(set.mock.calls).toEqual([[{ ports: [0], enabled: false }], [{ ports: [1], enabled: false }]])
  })
  it('no turn-off while a monitor runs: it would cut the capture, and the line says it comes after Stop', () => {
    mockState(lldpMonitor())
    render(<LldpNeighbours />)
    expect(screen.queryByRole('button', { name: /^Turn service mode off/ })).toBeNull()
    expect(screen.getByLabelText('LLDP service mode')).toHaveTextContent('Turn service mode off appears here once stopped.')
  })
  it('polls only while connected, and without a monitor says how to see neighbours', async () => {
    mockState({ ...lldpMonitor(), session: session({ state: 'disconnected' }) })
    render(<LldpNeighbours />)
    expect(api.getLldp).not.toHaveBeenCalled()
    cleanup()
    mockState({ session: session({ state: 'disconnected' }) })
    render(<LldpNeighbours />)
    expect(screen.getByText('Start the LLDP monitor to see neighbours.')).toBeInTheDocument()
    expect(START_HINT).toBe('Start the LLDP monitor to see neighbours.')
  })
  it('does not poll while no monitor runs: the table belongs to the monitor', async () => {
    mockState()
    render(<LldpNeighbours />)
    await new Promise((r) => setTimeout(r, 20))
    expect(api.getLldp).not.toHaveBeenCalled()
    expect(screen.getByText(START_HINT)).toBeInTheDocument()
    expect(screen.queryByText(/No LLDP neighbours seen/)).toBeNull()
    cleanup()
    mockState({ capture: { ...state.initialState.capture, monitor: { ...running, bpf: LLDP_BPF, gone: 'removed on the server' } } })
    render(<LldpNeighbours />)
    await new Promise((r) => setTimeout(r, 20))
    expect(api.getLldp).not.toHaveBeenCalled()
  })
  it('does not poll while the page is hidden, and polls once it is shown', async () => {
    mockState(lldpMonitor())
    const { rerender } = render(<LldpNeighbours active={false} />)
    await new Promise((r) => setTimeout(r, 20))
    expect(api.getLldp).not.toHaveBeenCalled()
    rerender(<LldpNeighbours active />)
    await screen.findByRole('table', { name: 'LLDP neighbours' })
    expect(api.getLldp).toHaveBeenCalledTimes(1)
  })
  it('with a monitor running and nothing heard, says so', async () => {
    vi.mocked(api.getLldp).mockResolvedValue({ ...reply, neighbours: [] })
    mockState({ capture: { ...state.initialState.capture, monitor: { ...running, bpf: LLDP_BPF } } })
    render(<LldpNeighbours />)
    expect(await screen.findByText('No LLDP neighbours seen by the current monitor.')).toBeInTheDocument()
    expect(screen.queryByText(START_HINT)).toBeNull()
  })
  it('one neighbour heard tagged and untagged is two rows', async () => {
    const one = reply.neighbours[1]
    vi.mocked(api.getLldp).mockResolvedValue({ ...reply, neighbours: [one, { ...one, vlan: 10 }] })
    const errors = vi.spyOn(console, 'error')
    mockState(lldpMonitor())
    render(<LldpNeighbours />)
    const table = await screen.findByRole('table', { name: 'LLDP neighbours' })
    expect(scoped(table).getAllByRole('row').slice(1).map((r) => scoped(r).getAllByRole('cell')[1].textContent)).toEqual(['', '10'])
    expect(errors).not.toHaveBeenCalled()
  })
  it('sorts by port, VLAN, then system name, says so on the header, and a header click re-sorts (RC1-18)', async () => {
    const n = reply.neighbours[1]
    const host = (vlan: number | null, mac: string) => ({ ...n, port: 0, vlan, system_name: 'host-a', chassis_id: '02:00:00:00:00:5c', port_id: mac, ttl: 120 })
    const router = { ...n, port: 0, vlan: null, system_name: 'edge-1', chassis_id: '02:00:00:00:00:b0', port_id: 'et-0/0/2', ttl: 90 }
    // the backend's order: port, VLAN, chassis ID, port ID
    vi.mocked(api.getLldp).mockResolvedValue({ ...reply, neighbours: [host(null, '02:00:00:00:00:01'), router,
      host(111, '02:00:00:00:00:02'), host(211, '02:00:00:00:00:03')] })
    mockState(lldpMonitor())
    render(<LldpNeighbours />)
    const table = await screen.findByRole('table', { name: 'LLDP neighbours' })
    const col = (i: number) => scoped(table).getAllByRole('row').slice(1).map((r) => scoped(r).getAllByRole('cell')[i].textContent)
    expect(col(2)).toEqual(['edge-1', 'host-a', 'host-a', 'host-a'])
    expect(col(1)).toEqual(['', '', '111', '211'])
    const header = (name: string) => scoped(table).getByRole('columnheader', { name })
    expect(header('Port')).toHaveAttribute('aria-sort', 'ascending')
    expect(header('VLAN')).toHaveAttribute('aria-sort', 'none')
    expect(header('Port').textContent).toBe('Port ▲')
    // a click sorts by that column; a second click reverses it; ties keep the default order
    await userEvent.click(scoped(header('TTL (s)')).getByRole('button'))
    expect(header('TTL (s)')).toHaveAttribute('aria-sort', 'ascending')
    expect(header('Port')).toHaveAttribute('aria-sort', 'none')
    expect(col(8)).toEqual(['90', '120', '120', '120'])
    expect(col(1)).toEqual(['', '', '111', '211'])
    await userEvent.click(scoped(header('TTL (s)')).getByRole('button'))
    expect(header('TTL (s)')).toHaveAttribute('aria-sort', 'descending')
    expect(col(8)).toEqual(['120', '120', '120', '90'])
    expect(col(1)).toEqual(['', '111', '211', ''])
    await userEvent.click(scoped(header('System name')).getByRole('button'))
    expect(col(2)).toEqual(['edge-1', 'host-a', 'host-a', 'host-a'])
  })
  it('not connected outranks the running monitor, and an Rx port off service is named', async () => {
    mockState({ session: session({ state: 'disconnected' }), capture: { ...state.initialState.capture, monitor: running } })
    const { unmount } = render(<LldpNeighbours />)
    expect(screen.getByRole('button', { name: 'Start LLDP monitor' })).toHaveAttribute('title', 'not connected')
    unmount()
    vi.restoreAllMocks()
    vi.spyOn(api, 'getLldp').mockResolvedValue(reply)
    mockState({ ports: [port(0, true), port(1, false)] })
    render(<LldpNeighbours />)
    await userEvent.click(scoped(screen.getByRole('group', { name: 'LLDP Rx ports' })).getByRole('checkbox', { name: 'Port 1' }))
    expect(screen.getByRole('button', { name: 'Start LLDP monitor' })).toHaveAttribute('title', 'Rx port(s) 1 are not in service mode')
  })
})
