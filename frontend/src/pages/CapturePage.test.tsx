import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import * as state from '../state'
import { captureStore } from '../captureStore'
import captureMsg from '../__fixtures__/captureMsg.json'
import lldpReply from '../__fixtures__/lldpNeighbours.json'
import { LLDP_BPF } from '../components/capture/LldpNeighbours'
import { CapturePage } from './CapturePage'

const port = (id: number): api.PortView => ({ id, state: 'IDLE', owner: 'me', owned: true, service: true, service_filtered: false } as api.PortView)
const session = { type: 'session', state: 'connected', params: null, info: null, error: null, rpc_console: false, include_publishing: false } as unknown as api.SessionSnapshot
const monitor = { ...(captureMsg.monitor_and_recorder.monitor as api.MonitorState), id: 2, bpf: LLDP_BPF }
const row = (id: number): api.CaptureRow => ({ id, state: 'STOPPED', mode: 'fixed', tx: [], rx: [1], bpf: '', count: 0, matched: 0, fetched: 0, limit: 100, bytes: 0, endpoint: null, mine: true, service: false })
const mockState = (withMonitor = false) => vi.spyOn(state, 'useAppState').mockReturnValue({
  ...state.initialState, session, ports: [port(0), port(1)],
  capture: { ...state.initialState.capture, monitor: withMonitor ? monitor : null, captures: [row(1), row(2), row(3)] } })
const tab = (name: RegExp | string) => screen.getByRole('tab', { name })

beforeEach(() => {
  captureStore.clear()
  vi.spyOn(api, 'wiresharkPreflight').mockResolvedValue({ ok: true, reason: null })
  vi.spyOn(api, 'getLldp').mockResolvedValue(lldpReply as api.LldpReply)
  vi.spyOn(api, 'captureFiles').mockResolvedValue({ dir: '/d', total: 3, files: [] })
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('CapturePage', () => {
  it('tabs are Live, Recorders, Saved files and LLDP with counts', async () => {
    mockState(true)                       // captures 1, 2 and 3; the monitor is capture 2, so two recorders
    render(<CapturePage />)
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual(['Live', 'Recorders (2)', 'Saved files (0)', 'LLDP'])
    expect(await screen.findByRole('tab', { name: 'Saved files (3)' })).toBeInTheDocument()
    expect(tab('Live')).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('heading', { name: 'Recorders', hidden: true })).not.toBeVisible()
    await userEvent.click(tab(/^Recorders/))
    expect(screen.getByRole('heading', { name: 'Recorders' })).toBeVisible()
  })
  it('the Live form survives a tab switch', async () => {
    mockState()
    render(<CapturePage />)
    await userEvent.click(screen.getAllByRole('checkbox', { name: 'Port 0' })[0])           // Tx ports
    await userEvent.type(screen.getByRole('textbox', { name: 'BPF filter' }), 'udp')
    await userEvent.click(tab('LLDP'))
    expect(screen.queryByRole('textbox', { name: 'BPF filter' })).toBeNull()      // hidden, not unmounted
    await userEvent.click(tab('Live'))
    expect(screen.getByRole('textbox', { name: 'BPF filter' })).toHaveValue('udp')
    expect(screen.getAllByRole('checkbox', { name: 'Port 0' })[0]).toBeChecked()
  })
  it('LLDP polls only while its tab is shown', async () => {
    mockState(true)
    const { rerender } = render(<CapturePage />)
    await new Promise((r) => setTimeout(r, 20))
    expect(api.getLldp).not.toHaveBeenCalled()                                     // Live is shown
    await userEvent.click(tab('LLDP'))
    await vi.waitFor(() => expect(api.getLldp).toHaveBeenCalledTimes(1))
    await userEvent.click(tab('Live'))
    vi.mocked(api.getLldp).mockClear()
    rerender(<CapturePage active={false} />)
    await userEvent.click(tab('LLDP'))                                             // the tab is picked, but the page is hidden
    await new Promise((r) => setTimeout(r, 20))
    expect(api.getLldp).not.toHaveBeenCalled()                                     // the page itself is hidden
  })
  it('the LLDP poll stops when the tab is left', async () => {
    mockState(true)
    render(<CapturePage />)
    await userEvent.click(tab('LLDP'))
    await vi.waitFor(() => expect(api.getLldp).toHaveBeenCalledTimes(1))
    await userEvent.click(tab('Live'))
    vi.mocked(api.getLldp).mockClear()
    await new Promise((r) => setTimeout(r, 2300))                                  // longer than one 2 s poll interval
    expect(api.getLldp).not.toHaveBeenCalled()
  })
  it('the Wireshark preflight runs only while Live is shown', async () => {
    mockState()
    render(<CapturePage />)
    await vi.waitFor(() => expect(api.wiresharkPreflight).toHaveBeenCalledTimes(1))
    await userEvent.click(tab(/^Recorders/))
    await userEvent.click(tab('LLDP'))
    await new Promise((r) => setTimeout(r, 20))
    expect(api.wiresharkPreflight).toHaveBeenCalledTimes(1)                        // not asked while another tab is shown
    await userEvent.click(tab('Live'))
    await vi.waitFor(() => expect(api.wiresharkPreflight).toHaveBeenCalledTimes(2))
  })
})
