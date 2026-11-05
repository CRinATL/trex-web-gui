import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import type { EmuMsg } from '../../api'
import { emuStore } from '../../emuStore'
import * as state from '../../state'
import emuMsg from '../../__fixtures__/emuMsg.json'
import { EmuConnect } from './EmuConnect'

const connected = emuMsg.connected as EmuMsg
const disconnected = emuMsg.disconnected as EmuMsg

beforeEach(() => { localStorage.clear(); act(() => emuStore.push(disconnected)) })
afterEach(() => { vi.restoreAllMocks(); localStorage.clear() })

const app = (over: Partial<state.AppState>) => vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, ...over })

describe('EmuConnect', () => {
  it('starts on the Control session host, else the server hint, else loopback; the port is 4510', () => {
    app({ session: { ...state.initialState.session, params: { host: 'sess-host', sync_port: 4501, async_port: 4500 } as never }, serverHint: 'hint-host' })
    const { unmount } = render(<EmuConnect />)
    expect(screen.getByLabelText(/^Host/)).toHaveValue('sess-host')
    expect(screen.getByLabelText(/^Port/)).toHaveValue('4510')
    unmount()
    app({ serverHint: 'hint-host' })
    const second = render(<EmuConnect />)
    expect(screen.getByLabelText(/^Host/)).toHaveValue('hint-host')
    second.unmount()
    app({})
    render(<EmuConnect />)
    expect(screen.getByLabelText(/^Host/)).toHaveValue('127.0.0.1')
  })
  it('connect posts host and port and remembers the target', async () => {
    app({})
    const connect = vi.spyOn(api, 'emuConnect').mockResolvedValue(connected)
    render(<EmuConnect />)
    await userEvent.clear(screen.getByLabelText(/^Host/))
    await userEvent.type(screen.getByLabelText(/^Host/), 'lab1')
    await userEvent.click(screen.getByRole('button', { name: 'Connect' }))
    expect(connect).toHaveBeenCalledWith('lab1', 4510)
    await vi.waitFor(() => expect(JSON.parse(localStorage.getItem('trex-web-gui.emuHosts') ?? '[]')).toEqual([{ host: 'lab1', port: 4510 }]))
  })
  it('shows the backend refusal text and remembers nothing', async () => {
    app({})
    vi.spyOn(api, 'emuConnect').mockRejectedValue(new api.ApiError(409, 'unsupported_server', "The EMU server's API is 2.0, not 1.x"))
    render(<EmuConnect />)
    await userEvent.click(screen.getByRole('button', { name: 'Connect' }))
    expect(await screen.findByRole('alert')).toHaveTextContent("The EMU server's API is 2.0, not 1.x")
    expect(localStorage.getItem('trex-web-gui.emuHosts')).toBeNull()
  })
  it('refuses a port outside 1..65535 and disconnects only when there is something to drop', async () => {
    app({})
    const disconnect = vi.spyOn(api, 'emuDisconnect').mockResolvedValue(disconnected)
    render(<EmuConnect />)
    await userEvent.clear(screen.getByLabelText(/^Port/))
    await userEvent.type(screen.getByLabelText(/^Port/), '70000')
    expect(screen.getByRole('button', { name: 'Connect' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Disconnect' })).toBeDisabled()
    act(() => emuStore.push(connected))
    await userEvent.click(screen.getByRole('button', { name: 'Disconnect' }))
    expect(disconnect).toHaveBeenCalled()
  })
})
