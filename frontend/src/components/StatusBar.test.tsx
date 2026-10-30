import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import type { SessionSnapshot } from '../api'
import { StatusBar } from './StatusBar'

afterEach(() => vi.restoreAllMocks())

const base: SessionSnapshot = { type: 'session', state: 'connected', params: null, info: null, error: null, rpc_console: false, include_polling: false }
const params = { host: 'h', sync_port: 4501, async_port: 4500, timeout_s: 3 }
const info = { version: 'v3.08', mode: 'STL', build_date: '', hostname: 'trex-vm', port_count: 16, core_type: '', ports: [] }
function bar(session: SessionSnapshot, o: { backendOnline?: boolean; owned?: number[]; onConnect?: () => void } = {}) {
  return render(<MemoryRouter><StatusBar backendOnline={o.backendOnline ?? true} session={session} owned={o.owned ?? []} onConnect={o.onConnect ?? (() => undefined)} /></MemoryRouter>)
}

describe('StatusBar', () => {
  it('connected with null params/info reads "Connected", never "Disconnected" under a connected colour', () => {
    bar(base)
    expect(screen.getByRole('status')).toHaveTextContent(/^Connected$/)
    expect(screen.getByRole('contentinfo')).toHaveClass('state-connected')
  })
  it('connected with params but no info shows the address only', () => {
    bar({ ...base, params })
    expect(screen.getByRole('status')).toHaveTextContent(/^Connected to h:4501$/)
  })
  it('connected shows Disconnect and the owned ports', async () => {
    const disconnect = vi.spyOn(api, 'disconnect').mockResolvedValue(base)
    bar({ ...base, params, info }, { owned: [0, 1] })
    expect(screen.getByRole('status')).toHaveTextContent('Connected to h:4501 · TRex v3.08 · 16 ports · you own 0, 1')
    expect(screen.queryByRole('button', { name: 'Connect…' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Disconnect' }))
    expect(disconnect).toHaveBeenCalled()
  })
  it('many owned ports are a count, with the total when known, and read-only is appended', () => {
    const { unmount } = bar({ ...base, params: { ...params, read_only: true }, info }, { owned: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11] })
    expect(screen.getByRole('status')).toHaveTextContent(/you own 12 of 16 · read-only$/)
    unmount()
    bar({ ...base, params }, { owned: [0, 1, 2, 3, 4] })
    expect(screen.getByRole('status')).toHaveTextContent(/you own 5 ports$/)
  })
  it('disconnected shows Connect…, disabled while the backend is offline', async () => {
    const onConnect = vi.fn()
    const { unmount } = bar({ ...base, state: 'disconnected' }, { backendOnline: false, onConnect })
    expect(screen.getByRole('button', { name: 'Connect…' })).toBeDisabled()
    expect(onConnect).not.toHaveBeenCalled()
    unmount()
    bar({ ...base, state: 'disconnected' }, { onConnect })
    expect(screen.getByRole('button', { name: 'Connect…' })).toHaveAttribute('data-run', 'go')   // green: the step to take (RC1-22)
    await userEvent.click(screen.getByRole('button', { name: 'Connect…' }))
    expect(onConnect).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('button', { name: 'Disconnect' })).toBeNull()
  })
  it('connecting offers Disconnect', async () => {
    const disconnect = vi.spyOn(api, 'disconnect').mockResolvedValue(base)
    bar({ ...base, state: 'connecting' })
    expect(screen.queryByRole('button', { name: 'Connect…' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Disconnect' }))
    expect(disconnect).toHaveBeenCalledTimes(1)
  })
  it('a second click while Disconnect is pending does not call the API again', async () => {
    let done!: (v: SessionSnapshot) => void
    const disconnect = vi.spyOn(api, 'disconnect').mockReturnValue(new Promise((r) => { done = r }))
    bar(base)
    await userEvent.dblClick(screen.getByRole('button', { name: 'Disconnect' }))
    expect(disconnect).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Disconnect' })).toBeDisabled()
    done(base)
  })
  it('a second click while Reconnect is pending does not call the API again', async () => {
    let done!: (v: { info: typeof info }) => void
    const connect = vi.spyOn(api, 'connect').mockReturnValue(new Promise((r) => { done = r }))
    bar({ ...base, state: 'lost', params, error: 'x' })
    await userEvent.dblClick(screen.getByRole('button', { name: 'Reconnect' }))
    expect(connect).toHaveBeenCalledTimes(1)
    done({ info })
  })
  it('a failure clears when the session state changes', async () => {
    vi.spyOn(api, 'disconnect').mockRejectedValue(new api.ApiError(500, 'x', 'boom'))
    const ui = (s: SessionSnapshot) => <MemoryRouter><StatusBar backendOnline session={s} owned={[]} onConnect={() => undefined} /></MemoryRouter>
    const { rerender } = render(ui(base))
    await userEvent.click(screen.getByRole('button', { name: 'Disconnect' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('boom')
    rerender(ui({ ...base, state: 'lost', params, error: 'x' }))
    expect(screen.queryByRole('alert')).toBeNull()
  })
  it('backend offline reads "Backend offline, reconnecting…" with Connect… disabled', () => {
    bar({ ...base, state: 'connected' }, { backendOnline: false })
    expect(screen.getByRole('status')).toHaveTextContent('Backend offline, reconnecting…')
    expect(screen.getByRole('button', { name: 'Connect…' })).toBeDisabled()
  })
  it('lost shows See Events, Reconnect and Connect…', () => {
    bar({ ...base, state: 'lost', params, error: 'subscriber timeout' })
    expect(screen.getByRole('status')).toHaveTextContent(/lost.*subscriber timeout/i)
    expect(screen.getByRole('link', { name: 'See Events' })).toHaveAttribute('href', '/events')
    expect(screen.getByRole('button', { name: 'Reconnect' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Connect…' })).toBeEnabled()
  })
  it('lost with no params has no Reconnect', () => {
    bar({ ...base, state: 'lost', params: null, error: 'x' })
    expect(screen.queryByRole('button', { name: 'Reconnect' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Connect…' })).toBeInTheDocument()
  })
  it('Reconnect calls connect with the lost session params', async () => {
    const lost = { ...params, read_only: true }
    const connect = vi.spyOn(api, 'connect').mockResolvedValue({ info })
    bar({ ...base, state: 'lost', params: lost, error: 'x' })
    await userEvent.click(screen.getByRole('button', { name: 'Reconnect' }))
    expect(connect).toHaveBeenCalledWith(lost)
  })
  it('Reconnect failing shows the backend message in the status bar and keeps the buttons', async () => {
    vi.spyOn(api, 'connect').mockRejectedValue(new api.ApiError(502, 'x', 'TRex is down'))
    const ui = (s: SessionSnapshot) => <MemoryRouter><StatusBar backendOnline session={s} owned={[]} onConnect={() => undefined} /></MemoryRouter>
    const { rerender } = render(ui({ ...base, state: 'lost', params, error: 'x' }))
    await userEvent.click(screen.getByRole('button', { name: 'Reconnect' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('TRex is down')
    expect(screen.getByRole('button', { name: 'Reconnect' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Connect…' })).toBeInTheDocument()
    // the backend's next snapshot after a failed connect: disconnected, same params, the error
    rerender(ui({ ...base, state: 'disconnected', params, error: 'TRex is down' }))
    expect(screen.getByRole('status')).toHaveTextContent(/Disconnected.*TRex is down/)
    expect(screen.getByRole('button', { name: 'Reconnect' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Connect…' })).toBeInTheDocument()
  })
  it('disconnected after a failed connect shows the error and offers Reconnect', () => {
    bar({ ...base, state: 'disconnected', params, error: 'TRex is down' })
    expect(screen.getByRole('status')).toHaveTextContent(/Disconnected.*TRex is down/)
    expect(screen.getByRole('button', { name: 'Reconnect' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'See Events' })).toHaveAttribute('href', '/events')
    expect(screen.getByRole('button', { name: 'Connect…' })).toBeEnabled()
  })
  it('disconnected with no error offers only Connect…', () => {
    bar({ ...base, state: 'disconnected', params })
    expect(screen.getByRole('status')).toHaveTextContent(/^Disconnected$/)
    expect(screen.queryByRole('button', { name: 'Reconnect' })).toBeNull()
    expect(screen.queryByRole('link', { name: 'See Events' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Connect…' })).toBeInTheDocument()
  })
  it('shows an error when disconnect fails', async () => {
    vi.spyOn(api, 'disconnect').mockRejectedValue(new api.ApiError(500, 'x', 'boom'))
    bar(base)
    await userEvent.click(screen.getByRole('button', { name: 'Disconnect' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('boom')
  })
})
