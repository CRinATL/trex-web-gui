import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import { wireLayerPort, wireNeighbours } from '../testWire'
import { NeighbourTable } from './NeighbourTable'

let busy: Record<number, api.BusyKind> = {}
let store: Record<number, api.Neighbour[]> = {}
const setNeighbours = vi.fn((port: number, list: api.Neighbour[]) => { store = { ...store, [port]: list } })
const clearNeighbours = vi.fn((port: number) => { const { [port]: _, ...rest } = store; void _; store = rest })
vi.mock('../state', () => ({
  useBusy: () => busy, useNeighbours: (p: number) => store[p],
  useNeighbourActions: () => ({ set: setNeighbours, clear: clearNeighbours }),
}))
beforeEach(() => { busy = {}; store = {}; setNeighbours.mockClear(); clearNeighbours.mockClear() })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

const port = (o: Partial<api.PortView> = {}) => wireLayerPort('vlan_two_tags', { owned: true, ...o })
const MAC = '52:52:18:89:91:ea', V6 = 'fe80::5052:18ff:fe89:91ea'

describe('NeighbourTable', () => {
  it('Scan fills the table from neighbours.json', async () => {
    vi.spyOn(api, 'scan6').mockResolvedValue(wireNeighbours())
    const { rerender } = render(<NeighbourTable port={port()} why={null} />)
    await userEvent.click(screen.getByRole('button', { name: 'Scan' }))
    expect(api.scan6).toHaveBeenCalledWith({ port: 0 })
    expect(setNeighbours).toHaveBeenCalledWith(0, wireNeighbours().neighbours)
    rerender(<NeighbourTable port={port()} why={null} />)
    const cells = screen.getAllByRole('cell').map((c) => c.textContent)
    expect(cells.slice(0, 3)).toEqual(['Host', MAC, V6])
    expect(screen.getAllByRole('columnheader').map((c) => c.textContent)).toEqual(['Type', 'MAC', 'IPv6', ''])
  })

  it('says so when a scan finds nothing, and Clear empties the table', async () => {
    store = { 0: [] }
    const { rerender } = render(<NeighbourTable port={port()} why={null} />)
    expect(screen.getByText('No IPv6 neighbours found.')).toBeInTheDocument()
    store = { 0: wireNeighbours().neighbours }
    rerender(<NeighbourTable port={port()} why={null} />)
    await userEvent.click(screen.getByRole('button', { name: 'Clear' }))
    expect(clearNeighbours).toHaveBeenCalledWith(0)
  })

  it('shows Scanning… while the port is scanning and disables Scan', () => {
    busy = { 0: 'scan' }
    render(<NeighbourTable port={port()} why="busy: scanning" />)
    expect(screen.getByRole('button', { name: 'Scanning…' })).toBeDisabled()
  })

  it('Set as L2 destination sends that MAC with the current VLAN', async () => {
    store = { 0: wireNeighbours().neighbours }
    const spy = vi.spyOn(api, 'applyLayer').mockResolvedValue({ ports: [], arp: null, service_restore_error: null })
    render(<NeighbourTable port={port()} why={null} />)
    await userEvent.click(screen.getByRole('button', { name: 'Set as L2 destination' }))
    expect(spy).toHaveBeenCalledWith({ port: 0, mode: 'L2', dst_mac: MAC, vlan: [10, 20] })
    expect(await screen.findByText(`L2 destination set to ${MAC}`)).toBeInTheDocument()
  })

  it('Scan does not read Scanning… while Set as L2 destination is in flight', async () => {
    store = { 0: wireNeighbours().neighbours }
    let done: (v: Awaited<ReturnType<typeof api.applyLayer>>) => void = () => undefined
    vi.spyOn(api, 'applyLayer').mockReturnValue(new Promise((r) => { done = r }))
    render(<NeighbourTable port={port()} why={null} />)
    await userEvent.click(screen.getByRole('button', { name: 'Set as L2 destination' }))
    expect(screen.getByRole('button', { name: 'Scan' })).toBeDisabled()
    expect(screen.queryByText('Scanning…')).toBeNull()
    done({ ports: [], arp: null, service_restore_error: null })
    expect(await screen.findByText(/L2 destination set/)).toBeInTheDocument()
  })

  it('Set as L2 destination is disabled with the reason', () => {
    store = { 0: wireNeighbours().neighbours }
    render(<NeighbourTable port={port()} why="read-only session" />)
    expect(screen.getByRole('button', { name: 'Set as L2 destination' })).toBeDisabled()
  })

  it('Copy MAC and Copy IPv6 write to the clipboard', async () => {
    store = { 0: wireNeighbours().neighbours }
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })
    render(<NeighbourTable port={port()} why={null} />)
    await userEvent.click(screen.getByRole('button', { name: 'Copy MAC' }))
    await userEvent.click(screen.getByRole('button', { name: 'Copy IPv6' }))
    expect(writeText).toHaveBeenNthCalledWith(1, MAC)
    expect(writeText).toHaveBeenNthCalledWith(2, V6)
  })

  it('Copy reports a refused clipboard', async () => {
    store = { 0: wireNeighbours().neighbours }
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } })
    render(<NeighbourTable port={port()} why={null} />)
    await userEvent.click(screen.getByRole('button', { name: 'Copy MAC' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Copy failed: denied')
  })

  it('the legacy-stack note shows for stack legacy', () => {
    const p = port()
    const { rerender } = render(<NeighbourTable port={{ ...p, info: { ...p.info, stack: 'legacy' } }} why={null} />)
    expect(screen.getByText('TRex ports on the legacy stack do not answer IPv6; only other devices are listed.')).toBeInTheDocument()
    rerender(<NeighbourTable port={{ ...p, info: { ...p.info, stack: 'linux_based' } }} why={null} />)
    expect(screen.queryByText(/legacy stack do not answer/)).toBeNull()
  })
})
