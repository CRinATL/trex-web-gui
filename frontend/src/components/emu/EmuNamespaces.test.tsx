import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import type { EmuMsg, EmuNamespaces as Body } from '../../api'
import { emuStore } from '../../emuStore'
import emuMsg from '../../__fixtures__/emuMsg.json'
import emuNamespaces from '../../__fixtures__/emuNamespaces.json'
import { EmuNamespaces } from './EmuNamespaces'

const connected = emuMsg.connected as EmuMsg
const body = emuNamespaces.two_namespaces as Body
const tick = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })

beforeEach(() => {
  vi.useFakeTimers()
  act(() => emuStore.push(connected))
})
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

describe('EmuNamespaces', () => {
  it('lists the namespaces, marks ours, and selects one', async () => {
    const get = vi.spyOn(api, 'emuNamespaces').mockResolvedValue({ ...body, namespaces: [body.namespaces[0], { ...body.namespaces[1], ours: true }, { ...body.namespaces[1], tci: [10, 20] }] })
    const onSelect = vi.fn()
    render(<EmuNamespaces selected={null} onSelect={onSelect} />)
    await tick(0)
    expect(get).toHaveBeenCalledTimes(1)
    expect(screen.getAllByRole('row')).toHaveLength(4)
    expect(screen.getAllByText('ours')).toHaveLength(1)
    expect(screen.getAllByText('arp, icmp, mdns')).toHaveLength(3)
    expect(screen.getByRole('button', { name: 'Clients of port 1, VLAN 10/20' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Clients of port 1' }))
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ vport: 1 }))
  })
  it('re-lists every 5 s and when the generation changes', async () => {
    const get = vi.spyOn(api, 'emuNamespaces').mockResolvedValue(body)
    render(<EmuNamespaces selected={null} onSelect={vi.fn()} />)
    await tick(0)
    await tick(4999)
    expect(get).toHaveBeenCalledTimes(1)
    await tick(1)
    expect(get).toHaveBeenCalledTimes(2)
    act(() => emuStore.push({ ...connected, generation: connected.generation + 1 }))
    await tick(0)
    expect(get).toHaveBeenCalledTimes(3)
  })
  it('says an interrupted listing, and a failed read, as notices', async () => {
    vi.spyOn(api, 'emuNamespaces').mockResolvedValueOnce({ ...body, interrupted: true }).mockRejectedValue(new api.ApiError(400, 'emu_error', 'too many'))
    render(<EmuNamespaces selected={null} onSelect={vi.fn()} />)
    await tick(0)
    expect(screen.getByRole('status')).toHaveTextContent('Another client was listing at the same time; showing what was read.')
    await tick(5000)
    expect(screen.getByRole('alert')).toHaveTextContent('Could not read the namespaces: too many')
    expect(screen.getAllByRole('row')).toHaveLength(3)
  })
})
