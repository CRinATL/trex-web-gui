import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import type { EmuMsg } from '../api'
import { emuStore } from '../emuStore'
import * as state from '../state'
import emuMsg from '../__fixtures__/emuMsg.json'
import emuClients from '../__fixtures__/emuClients.json'
import emuNamespaces from '../__fixtures__/emuNamespaces.json'
import emuCounters from '../__fixtures__/emuCounters.json'
import { EmuPage } from './EmuPage'

const connected = emuMsg.connected as EmuMsg
const disconnected = emuMsg.disconnected as EmuMsg

afterEach(() => vi.restoreAllMocks())
beforeEach(() => {
  vi.spyOn(api, 'emuNamespaces').mockResolvedValue({ generation: 0, namespaces: [], interrupted: false })
  vi.spyOn(api, 'emuCounters').mockResolvedValue(emuCounters.ctx as api.EmuCounters)
})

const online = (backendOnline: boolean) => vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, backendOnline })

describe('EmuPage', () => {
  it('mounts the profile block', () => {
    online(false)
    act(() => emuStore.push(disconnected))
    render(<EmuPage />)
    expect(screen.getByRole('heading', { name: 'Profile' })).toBeInTheDocument()
  })
  it('fetches the snapshot as it mounts (the hello does not carry it), then follows the hub messages', async () => {
    online(true)
    act(() => emuStore.push(disconnected))
    const get = vi.spyOn(api, 'getEmu').mockResolvedValue(connected)
    render(<EmuPage />)
    expect(screen.getByRole('heading', { name: 'EMU' })).toBeInTheDocument()
    expect(get).toHaveBeenCalledTimes(1)
    await vi.waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Connected'))
    act(() => emuStore.push(disconnected))
    expect(screen.getByRole('status')).toHaveTextContent('Disconnected')
  })
  it('asks nothing while the socket is down, and again when it comes back', async () => {
    const spy = online(false)
    act(() => emuStore.push(connected))
    const get = vi.spyOn(api, 'getEmu').mockResolvedValue(disconnected)
    const { rerender } = render(<EmuPage />)
    expect(get).not.toHaveBeenCalled()
    spy.mockReturnValue({ ...state.initialState, backendOnline: true })
    rerender(<EmuPage />)
    expect(get).toHaveBeenCalledTimes(1)
    await vi.waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Disconnected'))
    expect(emuStore.get()).toBe(disconnected)
  })
  it('drops the reply when a hub message arrived while the GET was in flight', async () => {
    online(true)
    act(() => emuStore.push(disconnected))
    let resolve: (m: EmuMsg) => void = () => undefined
    vi.spyOn(api, 'getEmu').mockReturnValue(new Promise((r) => { resolve = r }))
    render(<EmuPage />)
    act(() => emuStore.push({ ...connected, state: 'connecting' }))
    await act(async () => { resolve(disconnected); await Promise.resolve() })
    expect(screen.getByRole('status')).toHaveTextContent('Connecting')
  })
  it('drops the reply when the page was left before the GET answered', async () => {
    online(true)
    act(() => emuStore.push(disconnected))
    let resolve: (m: EmuMsg) => void = () => undefined
    vi.spyOn(api, 'getEmu').mockReturnValue(new Promise((r) => { resolve = r }))
    const { unmount } = render(<EmuPage />)
    unmount()
    await act(async () => { resolve(connected); await Promise.resolve() })
    expect(emuStore.get()).toBe(disconnected)
  })
  it('says a failed read until the next hub message', async () => {
    online(true)
    act(() => emuStore.push(disconnected))
    vi.spyOn(api, 'getEmu').mockRejectedValue(new api.ApiError(500, 'http_error', 'boom'))
    render(<EmuPage />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not read the EMU state: boom')
    act(() => emuStore.push(connected))
    expect(screen.queryByRole('alert')).toBeNull()
  })
  it('reads a client\'s counters once a namespace and a client are selected there', async () => {
    online(true)
    act(() => emuStore.push(connected))
    vi.spyOn(api, 'getEmu').mockResolvedValue(connected)
    vi.spyOn(api, 'emuNamespaces').mockResolvedValue(emuNamespaces.two_namespaces as api.EmuNamespaces)
    vi.spyOn(api, 'emuClients').mockResolvedValue(emuClients.vport_1_first_page as api.EmuClientsPage)
    const counters = vi.spyOn(api, 'emuCounters').mockResolvedValue(emuCounters.client as api.EmuCounters)
    render(<EmuPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Client' }))
    expect(screen.getByText(/Select a client/)).toBeInTheDocument()
    fireEvent.click((await screen.findAllByRole('button', { name: /^Clients of / }))[0])
    const pick = await screen.findAllByRole('button', { name: /^Select client / })
    fireEvent.click(pick[0])
    await vi.waitFor(() => expect(counters).toHaveBeenLastCalledWith('client', expect.objectContaining({ mac: expect.any(Array) })))
  })
  it('keeps the selected client when the already selected namespace is clicked again', async () => {
    online(true)
    act(() => emuStore.push(connected))
    vi.spyOn(api, 'getEmu').mockResolvedValue(connected)
    vi.spyOn(api, 'emuNamespaces').mockResolvedValue(emuNamespaces.two_namespaces as api.EmuNamespaces)
    vi.spyOn(api, 'emuClients').mockResolvedValue(emuClients.vport_1_first_page as api.EmuClientsPage)
    const counters = vi.spyOn(api, 'emuCounters').mockResolvedValue(emuCounters.client as api.EmuCounters)
    render(<EmuPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Client' }))
    const nsBtn = (await screen.findAllByRole('button', { name: /^Clients of / }))[0]
    fireEvent.click(nsBtn)
    fireEvent.click((await screen.findAllByRole('button', { name: /^Select client / }))[0])
    await vi.waitFor(() => expect(counters).toHaveBeenLastCalledWith('client', expect.anything()))
    fireEvent.click(nsBtn)
    expect(screen.queryByText(/Select a client above/)).toBeNull()
    expect(screen.getAllByRole('button', { name: /^Select client / })[0]).toHaveAttribute('aria-pressed', 'true')
  })
})
