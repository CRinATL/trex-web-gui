import { act, cleanup, fireEvent, render, screen, within as scoped } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import * as state from '../../state'
import { captureStore } from '../../captureStore'
import capturePkts from '../../__fixtures__/capturePkts.json'
import captureMsg from '../../__fixtures__/captureMsg.json'
import { CaptureMonitor } from './CaptureMonitor'

const port = (id: number, service: boolean): api.PortView => ({ id, state: 'IDLE', owner: 'me', owned: true, service, service_filtered: false } as api.PortView)
const session = (o: Partial<api.SessionSnapshot> = {}): api.SessionSnapshot =>
  ({ type: 'session', state: 'connected', params: null, info: null, error: null, rpc_console: false, include_publishing: false, ...o } as unknown as api.SessionSnapshot)
const running = captureMsg.monitor_and_recorder.monitor as api.MonitorState
const mockState = (o: Partial<state.AppState> = {}) => vi.spyOn(state, 'useAppState').mockReturnValue({
  ...state.initialState, session: session(), ports: [port(0, false), port(1, true)], ...o })
const withMonitor = (m: Partial<api.MonitorState>): Partial<state.AppState> =>
  ({ capture: { ...state.initialState.capture, monitor: { ...running, ...m } } })

beforeEach(() => {
  captureStore.clear()
  vi.spyOn(api, 'wiresharkPreflight').mockResolvedValue({ ok: true, reason: null })
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('CaptureMonitor', () => {
  it('Tx, Rx, BPF filter and Limit are rows of one form grid, Tx and Rx each on their own', () => {
    mockState()
    render(<CaptureMonitor onOpen={() => undefined} />)
    const tx = screen.getByRole('group', { name: 'Tx ports' }), rx = screen.getByRole('group', { name: 'Rx ports' })
    const grid = tx.closest('.form-grid')
    expect(grid).not.toBeNull()
    for (const el of [rx, screen.getByRole('textbox', { name: 'BPF filter' }), screen.getByRole('spinbutton', { name: 'Limit' })]) expect(el.closest('.form-grid')).toBe(grid)
    expect(tx.closest('.form-row')).not.toBe(rx.closest('.form-row'))
  })
  it('Start sends tx, rx, bpf, limit exactly', async () => {
    mockState()
    const start = vi.spyOn(api, 'monitorStart').mockResolvedValue({ monitor: running })
    render(<CaptureMonitor onOpen={() => undefined} />)
    await userEvent.click(within('Tx ports', 0))
    await userEvent.click(within('Rx ports', 1))
    await userEvent.type(screen.getByRole('textbox', { name: 'BPF filter' }), 'udp port 12')
    const limit = screen.getByRole('spinbutton', { name: 'Limit' })
    expect(limit).toHaveValue(100)
    await userEvent.clear(limit)
    await userEvent.type(limit, '250')
    await userEvent.click(screen.getByRole('button', { name: 'Start' }))
    expect(start).toHaveBeenCalledWith({ tx: [0], rx: [1], bpf: 'udp port 12', limit: 250 })
  })
  it('Start is disabled with the reason when rx port 1 is off service', async () => {
    mockState({ ports: [port(0, false), port(1, false)] })
    render(<CaptureMonitor onOpen={() => undefined} />)
    await userEvent.click(within('Rx ports', 1))
    const start = screen.getByRole('button', { name: 'Start' })
    expect(start).toBeDisabled()
    expect(start).toHaveAttribute('title', 'Rx port(s) 1 are not in service mode')
  })
  it('a Tx-only capture is not gated, and carries the help line', async () => {
    mockState({ ports: [port(0, false)] })
    render(<CaptureMonitor onOpen={() => undefined} />)
    await userEvent.click(within('Tx ports', 0))
    expect(screen.getByRole('button', { name: 'Start' })).toBeEnabled()
    expect(screen.getByText('On the af_packet test guest a Tx capture recorded nothing unless that port was in service mode; other NICs are unverified.')).toBeInTheDocument()
  })
  it('the service button sends one setService per port', async () => {
    mockState({ ports: [port(0, false), port(1, false), port(2, true)] })
    const svc = vi.spyOn(api, 'setService').mockResolvedValue({ ports: [] })
    render(<CaptureMonitor onOpen={() => undefined} />)
    await userEvent.click(within('Rx ports', 0))
    await userEvent.click(within('Rx ports', 1))
    await userEvent.click(within('Rx ports', 2))
    await userEvent.click(screen.getByRole('button', { name: 'Turn service mode on for ports 0, 1' }))
    expect(svc.mock.calls).toEqual([[{ ports: [0], enabled: true }], [{ ports: [1], enabled: true }]])
  })
  it('shows the gap between fetched and held rows', () => {
    captureStore.push({ type: 'capture_pkts', monitor_id: 2, rows: capturePkts.batch.rows as api.DecodedRow[] })   // 50 held
    mockState(withMonitor({ fetched: 80, matched: 80, count: 0 }))
    render(<CaptureMonitor onOpen={() => undefined} />)
    expect(screen.getByText('30 packets not shown')).toBeInTheDocument()
  })
  it('says nothing about the gap when the browser holds what was fetched', () => {
    captureStore.push({ type: 'capture_pkts', monitor_id: 2, rows: capturePkts.batch.rows as api.DecodedRow[] })
    mockState(withMonitor({ fetched: 50, matched: 50, count: 0 }))
    render(<CaptureMonitor onOpen={() => undefined} />)
    expect(screen.queryByText(/not shown/)).toBeNull()
  })
  it('clamps the gap and the server dropped count at 0 when fetched lags the rows or the status', () => {
    captureStore.push({ type: 'capture_pkts', monitor_id: 2, rows: capturePkts.batch.rows as api.DecodedRow[] })   // 50 held, fetched still 40
    mockState(withMonitor({ fetched: 40, matched: 40, count: 5 }))                                                  // raw dropped = -5
    render(<CaptureMonitor onOpen={() => undefined} />)
    expect(screen.queryByText(/not shown/)).toBeNull()
    expect(screen.getByText('Server dropped').nextElementSibling).toHaveTextContent('0')
  })
  it('shows the server dropped count and the browser dropped count', () => {
    mockState(withMonitor({ fetched: 100, matched: 1000, count: 0 }))
    render(<CaptureMonitor onOpen={() => undefined} />)
    expect(screen.getByText('Fetched').nextElementSibling).toHaveTextContent('100')
    expect(screen.getByText('Matched').nextElementSibling).toHaveTextContent('1000')
    expect(screen.getByText('Server dropped').nextElementSibling).toHaveTextContent('900')
    expect(screen.getByText('Browser dropped').nextElementSibling).toHaveTextContent('0')
  })
  it('the counters are one group, not a live region', () => {
    mockState(withMonitor({ fetched: 100, matched: 1000, count: 0 }))
    render(<CaptureMonitor onOpen={() => undefined} />)
    const line = screen.getByRole('group', { name: 'Counters' })
    expect(line).not.toHaveAttribute('role', 'status')
    expect(screen.queryByRole('status')).toBeNull()                    // a fetch every FETCH_S (0.5 s) must not be announced
    expect(line).toHaveClass('counters-line')
    for (const k of ['Fetched', 'Matched', 'Server dropped', 'Browser dropped']) expect(line).toContainElement(screen.getByText(k))
    expect(screen.queryByRole('term')).toBeNull()                      // no longer a definition list
  })
  it('the notes are behind ⓘ and the reason stays visible text under the actions', async () => {
    mockState({ ports: [port(0, false)] })
    render(<CaptureMonitor onOpen={() => undefined} />)
    expect(screen.getByText(/On the af_packet test guest/)).not.toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: /^About the af_packet/ }))
    expect(screen.getByText(/On the af_packet test guest/)).toBeVisible()
    expect(screen.getByText('choose at least one port')).toBeVisible()
  })
  it('shows removed on the server when gone', async () => {
    mockState(withMonitor({ gone: 'capture ID not found' }))
    render(<CaptureMonitor onOpen={() => undefined} />)
    expect(screen.getByRole('status')).toHaveTextContent('removed on the server')
    await userEvent.click(within('Rx ports', 1))
    expect(screen.getByRole('button', { name: 'Start' })).toBeEnabled()   // a gone monitor no longer blocks a new one
  })
  it('a limit above 10000 shows the memory note', async () => {
    mockState()
    render(<CaptureMonitor onOpen={() => undefined} />)
    expect(screen.queryByText(/9 KB of server memory/)).toBeNull()
    expect(screen.queryByRole('button', { name: 'About the limit' })).toBeNull()
    const limit = screen.getByRole('spinbutton', { name: 'Limit' })
    await userEvent.clear(limit)
    await userEvent.type(limit, '10001')
    expect(screen.getByText('Large captures use about 9 KB of server memory per packet')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'About the limit' })).toBeInTheDocument()
  })
  it('Download builds a pcap of the held rows', async () => {
    mockState()
    captureStore.push({ type: 'capture_pkts', monitor_id: 2, rows: capturePkts.batch.rows.slice(0, 2) as api.DecodedRow[] })
    const blobs: Blob[] = []
    vi.stubGlobal('URL', { ...URL, createObjectURL: (b: Blob) => { blobs.push(b); return 'blob:x' }, revokeObjectURL: () => undefined })
    const names: string[] = []
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { names.push(this.download) })
    render(<CaptureMonitor onOpen={() => undefined} />)
    await userEvent.click(screen.getByRole('button', { name: 'Download pcap' }))
    expect(names[0]).toMatch(/^monitor-\d{8}T\d{6}Z\.pcap$/)
    const bytes = new Uint8Array(await blobs[0].arrayBuffer())
    expect(Array.from(bytes.slice(0, 4))).toEqual([0xd4, 0xc3, 0xb2, 0xa1])   // little-endian magic
    expect(bytes.length).toBe(24 + 2 * (16 + 124))
  })
  it('Clear counts what it removed: 50 held at fetched 30, then fetched 50, shows no gap', async () => {
    const all = capturePkts.batch.rows as api.DecodedRow[]
    captureStore.push({ type: 'capture_pkts', monitor_id: 2, rows: all })   // 50 held, the status still says fetched 30
    const spy = mockState(withMonitor({ fetched: 30, matched: 30, count: 0 }))
    const { rerender } = render(<CaptureMonitor onOpen={() => undefined} />)
    await userEvent.click(screen.getByRole('button', { name: 'Clear' }))
    spy.mockReturnValue({ ...state.initialState, session: session(), ports: [port(0, false), port(1, true)], ...withMonitor({ fetched: 50, matched: 50, count: 0 }) })
    rerender(<CaptureMonitor onOpen={() => undefined} />)
    expect(screen.queryByText(/not shown/)).toBeNull()
    spy.mockReturnValue({ ...state.initialState, session: session(), ports: [port(0, false), port(1, true)], ...withMonitor({ fetched: 60, matched: 60, count: 0 }) })
    rerender(<CaptureMonitor onOpen={() => undefined} />)
    expect(screen.getByText('10 packets not shown')).toBeInTheDocument()   // a real gap still shows
  })
  it('a refused Start keeps the rows of the earlier monitor', async () => {
    mockState()
    captureStore.push({ type: 'capture_pkts', monitor_id: 2, rows: capturePkts.batch.rows as api.DecodedRow[] })
    vi.spyOn(api, 'monitorStart').mockRejectedValue(new api.ApiError(409, 'capture_slots', '9 of 10 server capture slots are used'))
    render(<CaptureMonitor onOpen={() => undefined} />)
    await userEvent.click(within('Rx ports', 1))
    await userEvent.click(screen.getByRole('button', { name: 'Start' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('9 of 10')
    expect(captureStore.rows()).toHaveLength(50)
  })
  it('an accepted Start empties the rows of the earlier monitor', async () => {
    mockState()
    captureStore.push({ type: 'capture_pkts', monitor_id: 2, rows: capturePkts.batch.rows as api.DecodedRow[] })
    vi.spyOn(api, 'monitorStart').mockResolvedValue({ monitor: running })
    render(<CaptureMonitor onOpen={() => undefined} />)
    await userEvent.click(within('Rx ports', 1))
    await userEvent.click(screen.getByRole('button', { name: 'Start' }))
    expect(captureStore.rows()).toHaveLength(0)
  })
  it('shows why Start is disabled as visible text, and keeps the title', async () => {
    mockState({ ports: [port(0, false), port(1, false)] })
    render(<CaptureMonitor onOpen={() => undefined} />)
    expect(screen.getByText('choose at least one port')).toBeVisible()
    await userEvent.click(within('Rx ports', 1))
    expect(screen.getByText('Rx port(s) 1 are not in service mode')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Start' })).toHaveAttribute('title', 'Rx port(s) 1 are not in service mode')
  })
  it('Stop is disabled once the monitor is gone', () => {
    mockState(withMonitor({ gone: 'capture ID not found' }))
    render(<CaptureMonitor onOpen={() => undefined} />)
    expect(screen.getByRole('button', { name: 'Stop' })).toBeDisabled()
  })
  it('revokes the download URL after the click has been handled', async () => {
    mockState()
    captureStore.push({ type: 'capture_pkts', monitor_id: 2, rows: capturePkts.batch.rows.slice(0, 1) as api.DecodedRow[] })
    const revoke = vi.fn()
    vi.stubGlobal('URL', { ...URL, createObjectURL: () => 'blob:x', revokeObjectURL: revoke })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    render(<CaptureMonitor onOpen={() => undefined} />)
    fireEvent.click(screen.getByRole('button', { name: 'Download pcap' }))   // synchronous: no timer has run yet
    expect(revoke).not.toHaveBeenCalled()
    await vi.waitFor(() => expect(revoke).toHaveBeenCalledWith('blob:x'))
  })
  it('the cleared count belongs to one connection: a reconnect reusing the monitor id starts from zero', async () => {
    const rowsOf = capturePkts.batch.rows as api.DecodedRow[]
    mockState({ ...withMonitor({ fetched: 50, matched: 50, count: 0 }), session: session({ connect_seq: 'a:1' }) })
    captureStore.push({ type: 'capture_pkts', monitor_id: 2, rows: rowsOf })
    const { rerender } = render(<CaptureMonitor onOpen={() => undefined} />)
    await userEvent.click(screen.getByRole('button', { name: 'Clear' }))
    expect(screen.queryByText(/not shown/)).toBeNull()
    // reconnect: same monitor id 2, the browser holds nothing, the server's fetched is 50 again
    mockState({ ...withMonitor({ fetched: 50, matched: 50, count: 0 }), session: session({ connect_seq: 'b:2' }) })
    rerender(<CaptureMonitor onOpen={() => undefined} />)
    expect(screen.getByText('50 packets not shown')).toBeInTheDocument()
  })
  it('the button is disabled with the preflight reason', async () => {
    const reason = 'no display on the backend host (DISPLAY/WAYLAND_DISPLAY unset)'
    vi.spyOn(api, 'wiresharkPreflight').mockResolvedValue({ ok: false, reason })
    mockState(withMonitor({}))
    render(<CaptureMonitor onOpen={() => undefined} />)
    const button = screen.getByRole('button', { name: 'Open in Wireshark' })
    await vi.waitFor(() => expect(button).toHaveAttribute('title', reason))
    expect(button).toBeDisabled()
    expect(screen.getByText(`Wireshark: ${reason}`)).toBeVisible()
  })
  it('the preflight is asked again each time the page is shown, so a Settings change is seen', async () => {
    const reason = 'wireshark not found: nope'
    const ask = vi.spyOn(api, 'wiresharkPreflight').mockResolvedValue({ ok: false, reason })
    mockState(withMonitor({}))
    const { rerender } = render(<CaptureMonitor onOpen={() => undefined} active={false} />)
    expect(ask).not.toHaveBeenCalled()                        // hidden: nothing asked
    rerender(<CaptureMonitor onOpen={() => undefined} active />)
    const button = screen.getByRole('button', { name: 'Open in Wireshark' })
    await vi.waitFor(() => expect(button).toHaveAttribute('title', reason))
    ask.mockResolvedValue({ ok: true, reason: null })        // the user fixed the path on the Settings page
    rerender(<CaptureMonitor onOpen={() => undefined} active={false} />)
    rerender(<CaptureMonitor onOpen={() => undefined} active />)
    await vi.waitFor(() => expect(button).toBeEnabled())
    expect(ask).toHaveBeenCalledTimes(2)
  })
  it('the button is disabled while the session is not connected', async () => {
    mockState({ ...withMonitor({}), session: session({ state: 'lost' }) })
    render(<CaptureMonitor onOpen={() => undefined} />)
    const button = screen.getByRole('button', { name: 'Open in Wireshark' })
    await vi.waitFor(() => expect(button).toHaveAttribute('title', 'not connected'))
    expect(button).toBeDisabled()
  })
  it('start sends POST and the snapshot shows running', async () => {
    const start = vi.spyOn(api, 'wiresharkStart').mockResolvedValue({ ...state.initialState.capture, wireshark: { running: true, error: null } })
    const spy = mockState(withMonitor({}))
    const { rerender } = render(<CaptureMonitor onOpen={() => undefined} />)
    const button = screen.getByRole('button', { name: 'Open in Wireshark' })
    await vi.waitFor(() => expect(button).toBeEnabled())
    await userEvent.click(button)
    expect(start).toHaveBeenCalledTimes(1)
    const on = withMonitor({})
    spy.mockReturnValue({ ...state.initialState, session: session(), ports: [port(0, false), port(1, true)], ...on,
      capture: { ...on.capture!, wireshark: { running: true, error: null } } })
    rerender(<CaptureMonitor onOpen={() => undefined} />)
    expect(screen.getByRole('button', { name: 'Stop Wireshark' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Open in Wireshark' })).toBeNull()
    // open, the Open button is lit and reads its state; the monitor's Start is lit too (RC1-20)
    expect(screen.getByRole('button', { name: 'Wireshark open' })).toHaveAttribute('data-on')
    expect(screen.getByRole('button', { name: 'Running' })).toHaveAttribute('data-run', 'go')
    expect(screen.getByRole('button', { name: 'Stop' })).toBeEnabled()
  })
  it('idle: Start is an unlit go button, Stop and Stop Wireshark are disabled stop buttons', () => {
    mockState()
    render(<CaptureMonitor onOpen={() => undefined} />)
    expect(screen.getByRole('button', { name: 'Start' })).not.toHaveAttribute('data-on')
    for (const name of ['Stop', 'Stop Wireshark']) {
      expect(screen.getByRole('button', { name })).toBeDisabled()
      expect(screen.getByRole('button', { name })).toHaveAttribute('data-run', 'stop')
    }
  })
  it('Stop and Clear', async () => {
    mockState(withMonitor({ fetched: 50, matched: 50, count: 0 }))
    captureStore.push({ type: 'capture_pkts', monitor_id: 2, rows: capturePkts.batch.rows as api.DecodedRow[] })
    const stop = vi.spyOn(api, 'monitorStop').mockResolvedValue({})
    render(<CaptureMonitor onOpen={() => undefined} />)
    await userEvent.click(screen.getByRole('button', { name: 'Stop' }))
    expect(stop).toHaveBeenCalledTimes(1)
    await userEvent.click(screen.getByRole('button', { name: 'Clear' }))
    expect(captureStore.rows()).toHaveLength(0)
    expect(screen.queryByText(/not shown/)).toBeNull()   // cleared on purpose, not lost
    act(() => captureStore.push({ type: 'capture_pkts', monitor_id: 2, rows: capturePkts.batch.rows.slice(0, 3) as api.DecodedRow[] }))
    expect(screen.getByRole('grid')).toBeInTheDocument()
  })
  it('the empty packet table says whether to start the monitor or wait', () => {
    mockState()
    render(<CaptureMonitor onOpen={() => undefined} />)
    expect(screen.getByRole('gridcell')).toHaveTextContent('No packets. Start the monitor to capture.')
  })
})

// The picker's checkbox for one port, by the group's name.
function within(group: string, id: number): HTMLElement {
  return scoped(screen.getByRole('group', { name: group })).getByRole('checkbox', { name: `Port ${id}` })
}
