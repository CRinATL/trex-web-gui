import { act, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useNavigate } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import { monitorStore } from '../monitorStore'
import daemonStf from '../__fixtures__/daemonMsgStf.json'
import monitorStateful from '../__fixtures__/monitorStateful.json'
import { wirePort, wireProfiles } from '../testWire'
import { trafficStore } from '../trafficStore'
import { ControlPage } from './ControlPage'

const connected = { type: 'session' as const, state: 'connected' as const, params: null, info: null, error: null, rpc_console: false, include_polling: false, ifaces: [], ifaces_note: null }
const port0: api.PortView = { id: 0, state: 'TX', owner: 'me', owned: true, service: false, service_filtered: false, link_up: true, speed_gbps: 10, promiscuous: false,
  multicast: false, fc_mode: 0, vlan: [], layer_cfg: {}, rx_filter_mode: null, profiles: wireProfiles('TX'), rx_info: {}, info: {} }
afterEach(() => { vi.restoreAllMocks(); trafficStore.reset() })

describe('ControlPage', () => {
  it('has no Connect or Disconnect button of its own without onConnect: they live in the status bar', () => {
    render(<ControlPage />)
    expect(screen.queryByRole('button', { name: 'Connect…' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Disconnect' })).toBeNull()
  })
  it('disconnected, with onConnect the line carries a green Connect… that opens the dialog (RC1-22)', async () => {
    const onConnect = vi.fn()
    render(<ControlPage onConnect={onConnect} />)
    const button = screen.getByRole('button', { name: 'Connect…' })
    expect(button).toHaveAttribute('data-run', 'go')
    expect(button.closest('p')).toHaveTextContent('Not connected. Use Connect… in the status bar (Ctrl+Shift+C) or click Connect….')
    await userEvent.click(button)
    expect(onConnect).toHaveBeenCalledOnce()
  })
  it('disconnected, the root view is replaced by the not-connected line', () => {
    render(<ControlPage />)
    expect(screen.getByText('Not connected. Use Connect… in the status bar (Ctrl+Shift+C).')).toBeInTheDocument()
  })
  it('lost, the line points at Reconnect, and a selected port panel stays below it', async () => {
    const state = await import('../state')
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, backendOnline: true, session: { ...connected, state: 'lost', error: 'x' }, ports: [port0] })
    render(<ControlPage onConnect={() => {}} />)
    expect(screen.getByText('Connection lost; Reconnect is in the status bar.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Connect…' })).toBeNull()   // lost: the way back is Reconnect
    expect(screen.queryByRole('alert')).toBeNull()
    await userEvent.click(screen.getByRole('treeitem', { name: /^Port 0,/ }))
    expect(screen.getByText('Connection lost; Reconnect is in the status bar.')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Control' })).toBeInTheDocument()
  })
  it('a selected port, then a disconnect: the column shows the not-connected line and no stale port', async () => {
    const state = await import('../state')
    const spy = vi.spyOn(state, 'useAppState')
    spy.mockReturnValue({ ...state.initialState, backendOnline: true, session: connected, ports: [port0] })
    const { rerender } = render(<ControlPage />)
    await userEvent.click(screen.getByRole('treeitem', { name: /^Port 0,/ }))
    expect(screen.getByRole('tab', { name: 'Control' })).toBeInTheDocument()
    spy.mockReturnValue({ ...state.initialState, backendOnline: true, session: { ...connected, state: 'disconnected' }, ports: [] })
    rerender(<ControlPage />)
    expect(screen.getByText(/^Not connected\./)).toBeInTheDocument()
    expect(screen.queryByText(/is not available/)).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Port 0' })).toBeNull()
    expect(screen.queryByRole('tab', { name: 'Control' })).toBeNull()
  })
  it('disconnected with an error and a way back, the line names Reconnect as the status bar offers it', async () => {
    const state = await import('../state')
    const params = { host: 'h', sync_port: 4501, async_port: 4500, timeout_s: 3 }
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, backendOnline: true, session: { ...connected, state: 'disconnected', error: 'refused', params }, ports: [] })
    render(<ControlPage />)
    expect(screen.getByText('Not connected. Use Reconnect or Connect… in the status bar (Ctrl+Shift+C).')).toBeInTheDocument()
  })
  it('connected shows no not-connected line', async () => {
    const state = await import('../state')
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, backendOnline: true, session: connected, ports: [port0] })
    render(<ControlPage />)
    expect(screen.queryByText(/Not connected|Connection lost/)).toBeNull()
  })
  it('a port error event raises the alert line, and the next successful command clears it', async () => {
    const state = await import('../state')
    vi.spyOn(api, 'stop').mockResolvedValue({ ports: [] })
    const spy = vi.spyOn(state, 'useAppState')
    const base = { ...state.initialState, backendOnline: true, session: connected, ports: [port0] }
    spy.mockReturnValue(base)
    const { rerender } = render(<ControlPage />)
    expect(screen.queryByRole('alert')).toBeNull()
    spy.mockReturnValue({ ...base, portError: { port: 0, seq: 1 }, portErrorSeq: 1 })
    rerender(<ControlPage />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Port 0 reported an error; see Events')
    await userEvent.click(screen.getByRole('treeitem', { name: /^Port 0,/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Stop' }))
    expect(screen.queryByRole('alert')).toBeNull()
  })
  it('an error after a disconnect and reconnect raises the alert again', async () => {
    const state = await import('../state')
    const spy = vi.spyOn(state, 'useAppState')
    const base = { ...state.initialState, backendOnline: true, session: connected, ports: [port0] }
    let s = state.reducer(base, { kind: 'msg', msg: { type: 'event', name: 'port error', args: [0], message: 'm', ts: 1, level: 'warning' } })
    spy.mockReturnValue(s)
    const { rerender } = render(<ControlPage />)
    // remount-free: mounted with seq 1 already present, so no alert
    expect(screen.queryByRole('alert')).toBeNull()
    s = state.reducer(s, { kind: 'msg', msg: { ...connected, state: 'disconnected' } })
    spy.mockReturnValue({ ...s, backendOnline: true })
    rerender(<ControlPage />)
    s = state.reducer(s, { kind: 'msg', msg: connected })
    s = state.reducer(s, { kind: 'msg', msg: { type: 'event', name: 'port error', args: [0], message: 'm', ts: 2, level: 'warning' } })
    spy.mockReturnValue({ ...s, backendOnline: true, ports: [port0] })
    rerender(<ControlPage />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Port 0 reported an error')
  })
  it('a remount does not replay an old port error', async () => {
    const state = await import('../state')
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, backendOnline: true, session: connected, ports: [port0], portError: { port: 0, seq: 3 }, portErrorSeq: 3 })
    render(<ControlPage />)
    expect(screen.queryByRole('alert')).toBeNull()
  })
  it('a request action leaves an existing alert in place and does not call the API', async () => {
    const state = await import('../state')
    const start = vi.spyOn(api, 'start')
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, backendOnline: true, session: connected, ports: [{ ...port0, state: 'STREAMS', profiles: wireProfiles('STREAMS') }] })
    render(<ControlPage />)
    await userEvent.click(screen.getByRole('treeitem', { name: /^Port 0,/ }))
    vi.spyOn(api, 'release').mockRejectedValue(new api.ApiError(409, 'x', 'still broken'))
    await userEvent.click(screen.getByRole('button', { name: 'Release' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('still broken')
    await userEvent.pointer({ keys: '[MouseRight]', target: screen.getByRole('treeitem', { name: /^Port 0,/ }) })
    await userEvent.click(screen.getByRole('menuitem', { name: 'Start…' }))
    expect(screen.getByRole('tab', { name: 'Traffic' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('alert')).toHaveTextContent('still broken')
    expect(start).not.toHaveBeenCalled()
  })
  it('with only Name… enabled (read-only), Escape closes the menu and the arrow keys do not move the selection', async () => {
    const state = await import('../state')
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, backendOnline: true, session: { ...connected, params: { host: 'h', sync_port: 1, async_port: 2, timeout_s: 3, read_only: true } }, ports: [port0] })
    render(<ControlPage />)
    const row = screen.getByRole('treeitem', { name: /^Port 0,/ })
    await userEvent.click(row)
    row.focus()
    await userEvent.keyboard('{Shift>}{F10}{/Shift}')
    expect(screen.getByRole('menuitem', { name: 'Name…' })).toHaveFocus()
    await userEvent.keyboard('{ArrowDown}')
    expect(row).toHaveAttribute('aria-selected', 'true')
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('menu')).toBeNull()
    expect(row).toHaveFocus()
  })
  it('a rejected command shows the backend message in the alert line', async () => {
    const state = await import('../state')
    vi.spyOn(api, 'stop').mockRejectedValue(new api.ApiError(409, 'x', 'port 0 is not owned'))
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, backendOnline: true, session: connected, ports: [port0] })
    render(<ControlPage />)
    await userEvent.click(screen.getByRole('treeitem', { name: /^Port 0,/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Stop' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('port 0 is not owned')
  })
  it('lays out the tree and the port panel; the log and RPC console are not on Control', async () => {
    const state = await import('../state')
    const stop = vi.spyOn(api, 'stop').mockResolvedValue({ ports: [] })
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, backendOnline: true, session: { ...connected, rpc_console: true, include_polling: false }, ports: [port0] })
    render(<ControlPage />)
    expect(screen.getByRole('tree')).toBeInTheDocument()
    expect(screen.queryByRole('log')).toBeNull()
    expect(screen.queryByText('RPC console')).toBeNull()
    await userEvent.pointer({ keys: '[MouseRight]', target: screen.getByRole('treeitem', { name: /^Port 0,/ }) })
    await userEvent.click(screen.getByRole('menuitem', { name: 'Stop' }))
    expect(stop).toHaveBeenCalledWith({ ports: [0] })
    expect(screen.queryByRole('menu')).toBeNull()
  })
})

describe('ControlPage request actions (Task 9 hooks, Task 10 wiring)', () => {
  const streams = { ...port0, state: 'STREAMS', profiles: wireProfiles('STREAMS') }
  const port1 = { ...port0, id: 1, state: 'STREAMS', profiles: wireProfiles('STREAMS') }
  async function mount(ports: api.PortView[]) {
    const state = await import('../state')
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, backendOnline: true, session: connected, ports })
    render(<ControlPage />)
  }
  const traffic = () => expect(screen.getByRole('tab', { name: 'Traffic' })).toHaveAttribute('aria-selected', 'true')
  async function menuOn(name: RegExp, item: string) {
    await userEvent.pointer({ keys: '[MouseRight]', target: screen.getByRole('treeitem', { name }) })
    await userEvent.click(screen.getByRole('menuitem', { name: item }))
    expect(screen.queryByRole('menu')).toBeNull()
  }

  it('toolbar Start with a bad form opens the Traffic tab with the error', async () => {
    const start = vi.spyOn(api, 'start')
    trafficStore.set(0, { multValue: '0' })
    await mount([streams])
    await userEvent.click(screen.getByRole('treeitem', { name: /^Port 0,/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Start' }))
    traffic()
    expect(await screen.findByText('Enter a multiplier greater than 0', { selector: '[role="alert"]' })).toBeInTheDocument()
    expect(start).not.toHaveBeenCalled()
  })
  it('toolbar Start with a good form starts without leaving the tab', async () => {
    const start = vi.spyOn(api, 'start').mockResolvedValue({ ports: [] })
    await mount([streams])
    await userEvent.click(screen.getByRole('treeitem', { name: /^Port 0,/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Start' }))
    expect(start).toHaveBeenCalledWith({ ports: [0], mult: '1', duration: -1 })
    expect(screen.getByRole('tab', { name: 'Control' })).toHaveAttribute('aria-selected', 'true')
  })
  it('menu Start… selects that port, opens Traffic and focuses the multiplier', async () => {
    await mount([streams, port1])
    await menuOn(/^Port 1,/, 'Start…')
    expect(screen.getByRole('treeitem', { name: /^Port 1,/ })).toHaveAttribute('aria-selected', 'true')
    traffic()
    expect(screen.getByLabelText('Multiplier')).toHaveFocus()
  })
  it('menu Assign profile… opens Traffic and focuses the profile picker', async () => {
    await mount([streams])
    await menuOn(/^Port 0,/, 'Assign profile…')
    traffic()
    expect(screen.getByRole('textbox', { name: /Profile path/ })).toHaveFocus()
  })
  it('menu Update… opens Traffic and focuses the multiplier', async () => {
    await mount([port0])
    await menuOn(/^Port 0,/, 'Update…')
    traffic()
    expect(screen.getByLabelText('Multiplier')).toHaveFocus()
  })
  it('opening the menu from the keyboard on an unselected row selects it, so Start… acts on that row', async () => {
    vi.spyOn(api, 'start').mockResolvedValue({ ports: [] })
    await mount([streams, port1])
    // Start all leaves the root selected
    await userEvent.click(screen.getByRole('button', { name: 'Start all' }))
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }))
    const row = screen.getByRole('treeitem', { name: /^Port 1,/ })
    row.focus()
    expect(row).toHaveAttribute('aria-selected', 'false')
    await userEvent.keyboard('{Shift>}{F10}{/Shift}')
    await userEvent.click(screen.getByRole('menuitem', { name: 'Start…' }))
    expect(row).toHaveAttribute('aria-selected', 'true')
    traffic()
    expect(screen.getByLabelText('Multiplier')).toHaveFocus()
  })
  it('asking twice moves focus again', async () => {
    await mount([streams])
    await menuOn(/^Port 0,/, 'Assign profile…')
    await userEvent.click(screen.getByLabelText('Multiplier'))
    await menuOn(/^Port 0,/, 'Assign profile…')
    expect(screen.getByRole('textbox', { name: /Profile path/ })).toHaveFocus()
  })
  it('Start all asks once, then starts every owned STREAMS port at the default multiplier', async () => {
    const st = vi.spyOn(api, 'start').mockResolvedValue({ ports: [] })
    const foreign = { ...port1, id: 2, owned: false, owner: 'bob' }
    const tx = { ...port0, id: 3 }
    await mount([streams, port1, foreign, tx])
    await userEvent.click(screen.getByRole('button', { name: 'Start all' }))
    expect(st).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog', { name: 'Start all ports' })).toBeInTheDocument()
    expect(within(screen.getByRole('dialog')).getByText("Each port's Traffic tab settings (rate, duration, Force) are not used.")).toBeInTheDocument()
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Start all' }))
    expect(st).toHaveBeenCalledTimes(1)
    expect(st).toHaveBeenCalledWith({ ports: [0, 1], mult: '1' })
    expect(screen.queryByRole('dialog')).toBeNull()
  })
  it('the Start all footer has Cancel before Start all, and the dialog is named by its heading', async () => {
    await mount([streams])
    await userEvent.click(screen.getByRole('button', { name: 'Start all' }))
    const dlg = screen.getByRole('dialog', { name: 'Start all ports' })
    expect(dlg).toHaveAttribute('aria-labelledby', 'start-all-title')
    expect([...dlg.querySelectorAll('.dialog-footer .end button')].map((b) => b.textContent)).toEqual(['Cancel', 'Start all'])
    expect(within(dlg).getByRole('button', { name: 'Start all' })).toHaveFocus()
  })
  it('a foreign profile alone in STREAMS (no `_` entry) leaves Start and Start all disabled', async () => {
    await mount([{ ...streams, profiles: { bench: 'STREAMS' } }])
    expect(screen.getByRole('button', { name: 'Start all' })).toBeDisabled()
    await userEvent.click(screen.getByRole('treeitem', { name: /^Port 0,/ }))
    expect(screen.getByRole('button', { name: 'Start' })).toBeDisabled()
  })
  it('the menu Start all asks the same question, and Cancel starts nothing', async () => {
    const st = vi.spyOn(api, 'start').mockResolvedValue({ ports: [] })
    await mount([streams])
    await userEvent.pointer({ keys: '[MouseRight]', target: screen.getByRole('treeitem', { name: /^TRex-/ }) })
    await userEvent.click(screen.getByRole('menuitem', { name: 'Start all' }))
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(st).not.toHaveBeenCalled()
  })
  it('Start all failing shows the backend message; opening the dialog did not clear an earlier alert', async () => {
    vi.spyOn(api, 'start').mockRejectedValue(new api.ApiError(409, 'x', 'port 1 is busy'))
    vi.spyOn(api, 'release').mockRejectedValue(new api.ApiError(409, 'x', 'earlier problem'))
    await mount([streams])
    await userEvent.click(screen.getByRole('treeitem', { name: /^Port 0,/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Release' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('earlier problem')
    await userEvent.click(screen.getByRole('treeitem', { name: /^TRex-/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Start all' }))
    expect(screen.getByRole('alert')).toHaveTextContent('earlier problem')
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Start all' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('port 1 is busy')
  })
})

describe('ControlPage while another page is shown (active=false)', () => {
  const streams = { ...port0, state: 'STREAMS', profiles: wireProfiles('STREAMS') }
  async function setup(ports: api.PortView[]) {
    const state = await import('../state')
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, backendOnline: true, session: connected, ports })
    const el = (active: boolean) => <ControlPage active={active} />
    return { ...render(el(true)), el }
  }
  it('a countdown does not fire while another page is shown', async () => {
    const up = vi.spyOn(api, 'update').mockResolvedValue({ ports: [] })
    const { rerender, el } = await setup([port0])
    await userEvent.click(screen.getByRole('treeitem', { name: /^Port 0,/ }))
    await userEvent.click(screen.getByRole('tab', { name: 'Traffic' }))
    vi.useFakeTimers()
    try {
      fireEvent.change(screen.getByLabelText('Multiplier'), { target: { value: '20 kpps' } })
      fireEvent.keyDown(screen.getByLabelText('Multiplier'), { key: 'Enter' })
      expect(screen.getByRole('status')).toHaveTextContent('Applying 20kpps')
      rerender(el(false))
      for (let i = 0; i < 20; i++) await act(async () => { vi.advanceTimersByTime(1000) })
      expect(up).not.toHaveBeenCalled()
    } finally { vi.useRealTimers() }
  })
  it('an open start-all dialog closes when the page is hidden', async () => {
    const { rerender, el } = await setup([streams])
    await userEvent.click(screen.getByRole('button', { name: 'Start all' }))
    expect(screen.getByRole('dialog', { name: 'Start all ports' })).toBeInTheDocument()
    rerender(el(false))
    expect(document.querySelector('[role="dialog"]')).toBeNull()
  })
  it('an open profile Browse dialog closes when the page is hidden', async () => {
    vi.spyOn(api, 'fsList').mockResolvedValue({ path: '/home/me', parent: '/home', entries: [], truncated: false })
    const { rerender, el } = await setup([streams])
    await userEvent.click(screen.getByRole('treeitem', { name: /^Port 0,/ }))
    await userEvent.click(screen.getByRole('tab', { name: 'Traffic' }))
    await userEvent.click(screen.getByRole('button', { name: 'Browse…' }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    rerender(el(false))
    expect(document.querySelector('[role="dialog"]')).toBeNull()
  })
  it('an open context menu closes when the page is hidden', async () => {
    const { rerender, el } = await setup([streams])
    await userEvent.pointer({ keys: '[MouseRight]', target: screen.getByRole('treeitem', { name: /^Port 0,/ }) })
    expect(screen.getByRole('menu')).toBeInTheDocument()
    rerender(el(false))
    expect(document.querySelector('[role="menu"]')).toBeNull()
  })
  describe('port names', () => {
    const params = { host: 'h', sync_port: 4501, async_port: 4500, timeout_s: 3, read_only: true }
    const real = wirePort('foreign_bench_tx_unowned', { id: 0 })
    const withLabels = async (labels: Record<string, Record<string, { name: string; description: string }>>, load_error: string | null = null) => {
      const state = await import('../state')
      vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, backendOnline: true, session: { ...connected, params }, ports: [real], portLabels: labels, portLabelsError: load_error })
    }
    const known = { 'h:4501': { '8e:4e:97:11:bf:a8': { name: 'uplink-A', description: 'to the DUT' } } }

    it('the port heading shows the name', async () => {
      await withLabels(known)
      render(<ControlPage />)
      await userEvent.click(screen.getByRole('treeitem', { name: /^Port 0 — uplink-A/ }))
      expect(screen.getByRole('heading', { name: 'Port 0 — uplink-A' })).toBeInTheDocument()
    })
    it('Name… from the context menu opens the Control tab with the Name field focused', async () => {
      await withLabels({}, 'bad toml')
      render(<ControlPage />)
      await userEvent.click(screen.getByRole('treeitem', { name: /^Port 0,/ }))
      await userEvent.click(screen.getByRole('tab', { name: 'Config' }))
      fireEvent.contextMenu(screen.getByRole('treeitem', { name: /^Port 0,/ }))
      await userEvent.click(screen.getByRole('menuitem', { name: 'Name…' }))
      expect(screen.queryByRole('menu')).toBeNull()
      expect(screen.queryByRole('dialog')).toBeNull()
      expect(screen.getByRole('tab', { name: 'Control' })).toHaveAttribute('aria-selected', 'true')
      expect(screen.getByRole('textbox', { name: /^Name/ })).toHaveFocus()
    })
    it('the System information table lists the name beside the port', async () => {
      const state = await import('../state')
      const info = { version: 'v', mode: 'Stateless', build_date: 'd', hostname: 'h', port_count: 1, core_type: 'c', ports: [{ index: 0, driver: 'drv' }] }
      vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, session: { ...connected, params, info }, ports: [real], portLabels: known })
      render(<ControlPage />)
      const cellEl = screen.getByText('drv').closest('tr')!.firstElementChild
      expect(cellEl).toHaveAttribute('title', 'to the DUT')
      expect(cellEl).toHaveAttribute('aria-label', 'Port 0 — uplink-A, to the DUT')
      expect(cellEl).toHaveTextContent('Port 0 — uplink-A')
    })
    it('an unsaved Name edit is reset by a new connect_seq', async () => {
      const state = await import('../state')
      const spy = vi.spyOn(state, 'useAppState')
      const at = (ports: api.PortView[], seq: string) => ({ ...state.initialState, backendOnline: true, session: { ...connected, params, connect_seq: seq }, ports })
      spy.mockReturnValue(at([real], 'a:1'))
      const { rerender } = render(<ControlPage />)
      await userEvent.click(screen.getByRole('treeitem', { name: /^Port 0,/ }))
      await userEvent.type(screen.getByRole('textbox', { name: /^Name/ }), 'draft')
      spy.mockReturnValue({ ...at([], 'a:1'), session: { ...connected, state: 'disconnected', params: null, connect_seq: 'a:1' } })
      rerender(<ControlPage />)
      spy.mockReturnValue(at([real], 'a:2'))
      rerender(<ControlPage />)
      expect(screen.getByRole('textbox', { name: /^Name/ })).toHaveValue('')
    })
    it('a new connect_seq with the same ports resets the edit as well', async () => {
      const state = await import('../state')
      const spy = vi.spyOn(state, 'useAppState')
      const at = (seq: string) => ({ ...state.initialState, backendOnline: true, session: { ...connected, params, connect_seq: seq }, ports: [real] })
      spy.mockReturnValue(at('a:1'))
      const { rerender } = render(<ControlPage />)
      await userEvent.click(screen.getByRole('treeitem', { name: /^Port 0,/ }))
      await userEvent.type(screen.getByRole('textbox', { name: /^Name/ }), 'draft')
      spy.mockReturnValue(at('a:2'))
      rerender(<ControlPage />)
      expect(screen.getByRole('textbox', { name: /^Name/ })).toHaveValue('')
    })
  })
  it('the setups dialog closes when the page goes inactive', async () => {
    const state = await import('../state')
    const session = { ...connected, params: { host: 'h', sync_port: 4501, async_port: 4500, timeout_s: 2 } }
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, backendOnline: true, session, ports: [port0] })
    const { rerender } = render(<ControlPage active />)
    await userEvent.click(screen.getByRole('button', { name: 'Setups ▾' }))
    await userEvent.click(screen.getByRole('menuitem', { name: 'Manage setups…' }))
    expect(screen.getByRole('dialog', { name: 'Test setups' })).toBeInTheDocument()
    rerender(<ControlPage active={false} />)
    expect(screen.queryByRole('dialog', { name: 'Test setups' })).toBeNull()
  })
  it('the Manage dialog stays open offline, with Load and Run disabled and a reason', async () => {
    const state = await import('../state')
    const params = { host: 'h', sync_port: 4501, async_port: 4500, timeout_s: 2 }
    const lab: api.TestSetup = { name: 'Lab A', slug: 'lab-a', created: 'c', updated: 'u', server: null, dashboard: null,
      ports: [{ key: '#0', port: 0, profile: { path: '/p/0.py', kind: 'py' }, tunables: { values: {}, extra: '' }, mult: '10kpps', duration: -1,
        force: false, acquire_force: false, stop_first: false, layer: null }] }
    const spy = vi.spyOn(state, 'useAppState')
    const on = { ...state.initialState, backendOnline: true, session: { ...connected, params }, ports: [port0], setups: [lab] }
    spy.mockReturnValue(on)
    const { rerender } = render(<ControlPage active />)
    await userEvent.click(screen.getByRole('button', { name: 'Setups ▾' }))
    await userEvent.click(screen.getByRole('menuitem', { name: 'Manage setups…' }))
    spy.mockReturnValue({ ...on, session: { ...connected, state: 'disconnected', params: null }, ports: [] })
    rerender(<ControlPage active />)
    const dlg = screen.getByRole('dialog', { name: 'Test setups' })
    const detail = within(within(dlg).getByRole('region', { name: 'Lab A' }))
    expect(detail.getByRole('button', { name: 'Load' })).toBeDisabled()
    expect(detail.getByRole('button', { name: 'Run' })).toBeDisabled()
    expect(detail.getByRole('button', { name: 'Rename' })).toBeEnabled()
    expect(detail.getByText('Connect first')).toBeInTheDocument()
  })
})

describe('ControlPage live update', () => {
  it('a pending update is dropped when its port leaves TX while another port is shown', async () => {
    const up = vi.spyOn(api, 'update').mockResolvedValue({ ports: [] })
    const state = await import('../state')
    const spy = vi.spyOn(state, 'useAppState')
    const port1: api.PortView = { ...port0, id: 1, state: 'STREAMS', profiles: wireProfiles('STREAMS') }
    const paused: api.PortView = { ...port0, state: 'PAUSE', profiles: wireProfiles('PAUSE') }
    const withPorts = (ports: api.PortView[]) => ({ ...state.initialState, backendOnline: true, session: connected, ports })
    spy.mockReturnValue(withPorts([port0, port1]))
    const { rerender } = render(<ControlPage />)
    await userEvent.click(screen.getByRole('treeitem', { name: /^Port 0,/ }))
    await userEvent.click(screen.getByRole('tab', { name: 'Traffic' }))
    fireEvent.change(screen.getByLabelText('Multiplier'), { target: { value: '20' } })
    fireEvent.keyDown(screen.getByLabelText('Multiplier'), { key: 'Enter' })
    expect(trafficStore.get(0).pending).not.toBeNull()
    await userEvent.click(screen.getByRole('treeitem', { name: /^Port 1,/ }))
    spy.mockReturnValue(withPorts([paused, port1]))
    rerender(<ControlPage />)
    expect(trafficStore.get(0).pending).toBeNull()
    vi.useFakeTimers()
    spy.mockReturnValue(withPorts([port0, port1]))
    rerender(<ControlPage />)
    await act(async () => { fireEvent.click(screen.getByRole('treeitem', { name: /^Port 0,/ })) })
    await act(async () => { vi.advanceTimersByTime(20000) })
    vi.useRealTimers()
    expect(up).not.toHaveBeenCalled()
    expect(screen.queryByRole('status')).toBeNull()
  })
})

describe('ControlPage from Studio', () => {
  function GoAssign() { const nav = useNavigate(); return <button type="button" onClick={() => nav('/control', { state: { studioAssign: { port: 0 } } })}>go</button> }
  it('a studioAssign request selects the port\'s Traffic tab', async () => {
    const state = await import('../state')
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, backendOnline: true, session: connected, ports: [{ ...port0, state: 'STREAMS', profiles: wireProfiles('STREAMS') }] })
    render(<MemoryRouter initialEntries={['/studio']}><ControlPage /><GoAssign /></MemoryRouter>)
    expect(screen.queryByRole('tab', { name: 'Traffic' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'go' }))
    expect(screen.getByRole('tab', { name: 'Traffic' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('textbox', { name: /Profile path/ })).toHaveFocus()
    // read once: moving to another tab and re-rendering does not pull it back
    await userEvent.click(screen.getByRole('tab', { name: 'Control' }))
    expect(screen.getByRole('tab', { name: 'Control' })).toHaveAttribute('aria-selected', 'true')
  })
})

describe('ControlPage and the TRex daemon', () => {
  it('TRex daemon… opens the dialog while disconnected', async () => {
    const state = await import('../state')
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, backendOnline: true, daemonHint: { host: '127.0.0.1', port: 8090 } })
    vi.spyOn(api, 'daemonStartPreview').mockResolvedValue({ command_preview: 'nice' })
    vi.spyOn(api, 'daemonDisconnect').mockResolvedValue({} as api.DaemonMsg)
    render(<ControlPage />)
    expect(screen.queryByRole('dialog', { name: 'TRex daemon' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'TRex daemon…' }))
    expect(screen.getByRole('dialog', { name: 'TRex daemon' })).toBeInTheDocument()
    expect(screen.getByLabelText('Host')).toHaveValue('127.0.0.1')
    await userEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog', { name: 'TRex daemon' })).toBeNull()
    expect(api.daemonDisconnect).toHaveBeenCalled()
  })
  it('a hidden page closes the dialog and disconnects', async () => {
    const state = await import('../state')
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, backendOnline: true })
    vi.spyOn(api, 'daemonStartPreview').mockResolvedValue({ command_preview: 'nice' })
    const disconnect = vi.spyOn(api, 'daemonDisconnect').mockResolvedValue({} as api.DaemonMsg)
    const { rerender } = render(<ControlPage />)
    await userEvent.click(screen.getByRole('button', { name: 'TRex daemon…' }))
    expect(disconnect).not.toHaveBeenCalled()
    rerender(<ControlPage active={false} />)
    expect(screen.queryByRole('dialog', { name: 'TRex daemon' })).toBeNull()
    expect(disconnect).toHaveBeenCalledTimes(1)
  })
  it('a stateful run on the hint host shows the Monitor banner', async () => {
    const state = await import('../state')
    const base = { ...state.initialState, backendOnline: true, serverHint: '127.0.0.1' }
    const spy = vi.spyOn(state, 'useAppState').mockReturnValue({ ...base, daemon: daemonStf as unknown as api.DaemonMsg })
    const { rerender } = render(<ControlPage />)
    const banner = screen.getByText(/127\.0\.0\.1 is running a stateful \(-f\) run\. It has no command port; watch it on the Monitor\./)
    expect(banner).toBeInTheDocument()
    expect(within(banner).getByRole('link', { name: 'Open Monitor' })).toHaveAttribute('href', '/monitor')
    // another host, or a connected session: no banner
    spy.mockReturnValue({ ...base, serverHint: 'lab9', daemon: daemonStf as unknown as api.DaemonMsg }); rerender(<ControlPage />)
    expect(screen.queryByText(/is running a stateful/)).toBeNull()
    spy.mockReturnValue({ ...base, session: connected, daemon: daemonStf as unknown as api.DaemonMsg }); rerender(<ControlPage />)
    expect(screen.queryByText(/is running a stateful/)).toBeNull()
  })
  it('the Monitor seeing STF names also shows the banner', async () => {
    const state = await import('../state')
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, backendOnline: true, serverHint: '127.0.0.1' })
    monitorStore.reset()
    render(<ControlPage />)
    expect(screen.queryByText(/is running a stateful/)).toBeNull()
    act(() => monitorStore.push(monitorStateful as unknown as api.MonitorMsg))
    expect(screen.getByText(/is running a stateful/)).toBeInTheDocument()
    monitorStore.reset()
  })
})
