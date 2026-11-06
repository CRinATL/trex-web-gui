import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import type { EmuClient, EmuClientsPage, EmuMsg } from '../../api'
import { emuStore } from '../../emuStore'
import emuMsg from '../../__fixtures__/emuMsg.json'
import emuClients from '../../__fixtures__/emuClients.json'
import { EmuClients } from './EmuClients'

const connected = emuMsg.connected as EmuMsg
const page = emuClients.vport_1_first_page as EmuClientsPage
const ns = { vport: 1, tci: [0, 0], tpid: [0, 0] }
const tick = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })

beforeEach(() => {
  vi.useFakeTimers()
  act(() => emuStore.push(connected))
})
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

describe('EmuClients', () => {
  it('lists the fixture clients with the gateway unresolved', async () => {
    const get = vi.spyOn(api, 'emuClients').mockResolvedValue(page)
    render(<EmuClients ns={ns} onGone={vi.fn()} />)
    await tick(0)
    expect(get).toHaveBeenCalledWith(ns, 0, 100)
    expect(screen.getByText('00:00:00:01:00:00')).toBeInTheDocument()
    expect(screen.getAllByText('unresolved')).toHaveLength(page.clients.length)
    expect(screen.getByText(`Showing 1–${page.clients.length} of ${page.total}`)).toBeInTheDocument()
  })
  it('says a resolved gateway with its MAC, and shows only the IPv6 fields that are set', async () => {
    const c: EmuClient = {
      ...page.clients[0],
      dgw: { resolve: true, rmac: [0xaa, 0xbb, 0xcc, 0, 1, 2] },
      ipv6: [0x20, 0x01, 0x0d, 0xb8, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
    }
    vi.spyOn(api, 'emuClients').mockResolvedValue({ ...page, clients: [c] })
    render(<EmuClients ns={ns} onGone={vi.fn()} />)
    await tick(0)
    expect(screen.getByText('resolved aa:bb:cc:00:01:02')).toBeInTheDocument()
    expect(screen.getByText('addr 2001:db8::1')).toBeInTheDocument()
    expect(screen.queryByText(/^slaac|^dhcp|^dg /)).toBeNull()
  })
  it('pages 100 at a time, sending offset and limit', async () => {
    const get = vi.spyOn(api, 'emuClients').mockImplementation(async (_n, offset) => ({ ...page, total: 250, clients: page.clients.slice(0, 1), interrupted: offset === 100 }))
    render(<EmuClients ns={ns} onGone={vi.fn()} />)
    await tick(0)
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    await tick(0)
    expect(get).toHaveBeenLastCalledWith(ns, 100, 100)
    expect(screen.getByText('Showing 101–101 of 250')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('showing what was read')
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    await tick(0)
    expect(get).toHaveBeenLastCalledWith(ns, 200, 100)
    expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Previous page' }))
    await tick(0)
    expect(get).toHaveBeenLastCalledWith(ns, 100, 100)
  })
  it('says an empty page past the end, with Previous still available', async () => {
    vi.spyOn(api, 'emuClients').mockImplementation(async (_n, offset) => ({ ...page, total: 150, clients: offset === 0 ? page.clients : [] }))
    render(<EmuClients ns={ns} onGone={vi.fn()} />)
    await tick(0)
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    await tick(0)
    expect(screen.getByText('No clients on this page.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeEnabled()
  })
  it('shows no rows of the old page under the new page label when the new read fails', async () => {
    const get = vi.spyOn(api, 'emuClients').mockResolvedValue({ ...page, total: 250 })
    render(<EmuClients ns={ns} onGone={vi.fn()} />)
    await tick(0)
    expect(screen.getByText('00:00:00:01:00:00')).toBeInTheDocument()
    get.mockRejectedValue(new api.ApiError(400, 'emu_error', 'bad thing'))
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    await tick(0)
    expect(screen.queryByText('00:00:00:01:00:00')).toBeNull()
    expect(screen.getByRole('alert')).toHaveTextContent('bad thing')
  })
  it('names a namespace by its port and VLAN tags', async () => {
    vi.spyOn(api, 'emuClients').mockResolvedValue(page)
    render(<EmuClients ns={{ vport: 1, tci: [10, 20], tpid: [0, 0] }} onGone={vi.fn()} />)
    await tick(0)
    expect(screen.getByRole('heading', { name: 'Clients of port 1, VLAN 10/20' })).toBeInTheDocument()
  })
  it('re-reads every 5 s', async () => {
    const get = vi.spyOn(api, 'emuClients').mockResolvedValue(page)
    render(<EmuClients ns={ns} onGone={vi.fn()} />)
    await tick(0)
    await tick(5000)
    expect(get).toHaveBeenCalledTimes(2)
  })
  it('clears the selection on 409 no_namespace; any other failure is a notice', async () => {
    const get = vi.spyOn(api, 'emuClients').mockRejectedValue(new api.ApiError(400, 'emu_error', 'bad thing'))
    const onGone = vi.fn()
    render(<EmuClients ns={ns} onGone={onGone} />)
    await tick(0)
    expect(screen.getByRole('alert')).toHaveTextContent('Could not read the clients: bad thing')
    expect(onGone).not.toHaveBeenCalled()
    get.mockRejectedValue(new api.ApiError(409, 'no_namespace', 'gone'))
    await tick(5000)
    expect(onGone).toHaveBeenCalled()
    expect(screen.queryByRole('alert')).toBeNull()
  })
  it('re-reads at once when the generation changes, so a removed namespace clears the selection then', async () => {
    const get = vi.spyOn(api, 'emuClients').mockResolvedValue(page)
    const onGone = vi.fn()
    render(<EmuClients ns={ns} onGone={onGone} />)
    await tick(0)
    expect(get).toHaveBeenCalledTimes(1)
    get.mockRejectedValue(new api.ApiError(409, 'no_namespace', 'gone'))
    act(() => emuStore.push({ ...connected, generation: connected.generation + 1 }))   // a Remove all… from this app or another
    await tick(0)
    expect(get).toHaveBeenCalledTimes(2)
    expect(onGone).toHaveBeenCalled()
  })
  it('offers a Select button per client only when asked, and marks the selected one', async () => {
    vi.spyOn(api, 'emuClients').mockResolvedValue(page)
    const pick = vi.fn()
    const { rerender } = render(<EmuClients ns={ns} onGone={vi.fn()} />)
    await tick(0)
    expect(screen.queryByRole('button', { name: /^Select client/ })).toBeNull()
    rerender(<EmuClients ns={ns} onGone={vi.fn()} onSelectClient={pick} selectedMac={page.clients[1].mac} />)
    const btns = screen.getAllByRole('button', { name: /^Select client / })
    expect(btns).toHaveLength(page.clients.length)
    expect(btns[1]).toHaveAttribute('aria-pressed', 'true')
    expect(btns[0]).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(btns[0])
    expect(pick).toHaveBeenCalledWith(page.clients[0].mac)
  })
})
