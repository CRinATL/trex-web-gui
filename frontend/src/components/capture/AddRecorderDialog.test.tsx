import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import * as state from '../../state'
import { AddRecorderDialog } from './AddRecorderDialog'

const port = (id: number, service: boolean): api.PortView => ({ id, state: 'IDLE', owner: 'me', owned: true, service, service_filtered: false } as api.PortView)
const session = (o: Partial<api.SessionSnapshot> = {}): api.SessionSnapshot =>
  ({ type: 'session', state: 'connected', params: null, info: null, error: null, rpc_console: false, include_publishing: false, ...o } as unknown as api.SessionSnapshot)
const row = (id: number): api.CaptureRow => ({ id, state: 'STOPPED', mode: 'fixed', tx: [], rx: [1], bpf: '', count: 0, matched: 0, fetched: 0, limit: 10, bytes: 0, endpoint: null, mine: false, service: false })
const mockState = (o: Partial<state.AppState> = {}) => vi.spyOn(state, 'useAppState').mockReturnValue({
  ...state.initialState, session: session(), ports: [port(0, false), port(1, true)], ...o })
const within = (group: string, i: number) => screen.getAllByRole('checkbox', { name: new RegExp(`Port ${i}`) })[group === 'Tx ports' ? 0 : 1]

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('AddRecorderDialog', () => {
  it('sends tx, rx, bpf, limit 1000 by default', async () => {
    mockState()
    const start = vi.spyOn(api, 'recorderStart').mockResolvedValue({ id: 3 })
    const onClose = vi.fn()
    render(<AddRecorderDialog onClose={onClose} />)
    await userEvent.click(within('Tx ports', 0))
    await userEvent.click(within('Rx ports', 1))
    await userEvent.type(screen.getByRole('textbox', { name: 'BPF filter' }), 'udp')
    expect(screen.getByRole('spinbutton', { name: 'Limit' })).toHaveValue(1000)
    await userEvent.click(screen.getByRole('button', { name: 'OK' }))
    expect(start).toHaveBeenCalledWith({ tx: [0], rx: [1], bpf: 'udp', limit: 1000 })
    expect(onClose).toHaveBeenCalled()
  })
  it('the footer has Cancel before the main action; the af_packet note is behind ⓘ', () => {
    mockState()
    render(<AddRecorderDialog onClose={() => undefined} />)
    const dlg = screen.getByRole('dialog', { name: 'Add recorder' })
    expect([...dlg.querySelectorAll('.dialog-footer .end button')].map((b) => b.textContent)).toEqual(['Cancel', 'OK'])
    expect(screen.getByText(/af_packet test guest/)).not.toBeVisible()
  })
  it('gated like the monitor', async () => {
    mockState({ ports: [port(0, false), port(1, false)] })
    const svc = vi.spyOn(api, 'setService').mockResolvedValue({ ports: [] })
    render(<AddRecorderDialog onClose={() => undefined} />)
    expect(screen.getByRole('button', { name: 'OK' })).toBeDisabled()   // no port chosen
    await userEvent.click(within('Rx ports', 1))
    const ok = screen.getByRole('button', { name: 'OK' })
    expect(ok).toBeDisabled()
    expect(ok).toHaveAttribute('title', 'Rx port(s) 1 are not in service mode')
    await userEvent.click(screen.getByRole('button', { name: 'Turn service mode on for ports 1' }))
    expect(svc).toHaveBeenCalledWith({ ports: [1], enabled: true })
  })
  it('nine slots used disables OK with the note', async () => {
    mockState({ capture: { ...state.initialState.capture, captures: Array.from({ length: 9 }, (_, i) => row(i + 1)) } })
    render(<AddRecorderDialog onClose={() => undefined} />)
    await userEvent.click(within('Tx ports', 0))
    expect(screen.getByText('9 of 10 server slots used; one is kept for ping, ARP and scan')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'OK' })).toBeDisabled()
  })
  it('Tx and Rx are rows of the one form grid with BPF filter, each on its own row', () => {
    mockState()
    render(<AddRecorderDialog onClose={() => undefined} />)
    const tx = screen.getByRole('group', { name: 'Tx ports' }), rx = screen.getByRole('group', { name: 'Rx ports' })
    const grid = tx.closest('.form-grid')
    expect(grid).not.toBeNull()
    expect(rx.closest('.form-grid')).toBe(grid)
    expect(screen.getByRole('textbox', { name: 'BPF filter' }).closest('.form-grid')).toBe(grid)
    expect(tx.closest('.form-row')).not.toBe(rx.closest('.form-row'))
  })
  it('shows a refusal and stays open', async () => {
    mockState()
    vi.spyOn(api, 'recorderStart').mockRejectedValue(new Error('capture limit has reached'))
    const onClose = vi.fn()
    render(<AddRecorderDialog onClose={onClose} />)
    await userEvent.click(within('Tx ports', 0))
    await userEvent.click(screen.getByRole('button', { name: 'OK' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('capture limit has reached')
    expect(onClose).not.toHaveBeenCalled()
  })
})
