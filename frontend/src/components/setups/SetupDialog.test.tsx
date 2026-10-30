import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import type { ConnectParams, PortView, SetupPort, TestSetup } from '../../api'
import { runInProgress, setupSession } from '../../setupSession'
import { trafficStore } from '../../trafficStore'
import { SetupDialog } from './SetupDialog'

afterEach(() => { vi.restoreAllMocks(); trafficStore.reset() })

const params: ConnectParams = { host: 'h', sync_port: 4501, async_port: 4500, timeout_s: 3 }
const mac = (id: number) => `aa:bb:cc:dd:ee:0${id}`
const view = (id: number): PortView => ({
  id, state: 'IDLE', owner: 'me', owned: true, service: false, service_filtered: false, link_up: true, speed_gbps: 10, promiscuous: false,
  multicast: false, fc_mode: 0, vlan: [], layer_cfg: {}, rx_filter_mode: null, profiles: {}, rx_info: {}, info: { hw_mac: mac(id) },
})
const ports = [view(0), view(1)]
const sp = (port: number, o: Partial<SetupPort> = {}): SetupPort => ({
  key: mac(port), port, profile: { path: `/p/${port}.py`, kind: 'py' }, tunables: { values: {}, extra: '' },
  mult: '10kpps', duration: -1, force: false, acquire_force: false, stop_first: false, layer: null, ...o,
})
const lab = (list: SetupPort[] = [sp(0), sp(1)]): TestSetup =>
  ({ name: 'Lab A', slug: 'lab-a', created: 'c', updated: 'u1', server: 'h:4501', ports: list, dashboard: null })
const inspected = (path: string): api.Inspected => ({ path, kind: 'py', tunables: [], summary: [], stream_count: 1 })

function open(o: { setups?: TestSetup[]; serverPorts?: PortView[]; readOnly?: boolean; loadErrors?: { file: string; error: string }[] } = {}) {
  vi.spyOn(api, 'inspect').mockImplementation(async (b) => inspected(b.path))
  const spies = {
    acquire: vi.spyOn(api, 'acquire').mockResolvedValue({ ports }),
    stop: vi.spyOn(api, 'stop').mockResolvedValue({ ports }),
    applyLayer: vi.spyOn(api, 'applyLayer').mockResolvedValue({ ports, arp: null, service_restore_error: null }),
    assign: vi.spyOn(api, 'assign').mockResolvedValue({ summary: [], stream_count: 1, rate: {}, ports }),
    start: vi.spyOn(api, 'start').mockResolvedValue({ ports }),
  }
  const onClose = vi.fn()
  const element = (list: TestSetup[]) => <SetupDialog setups={list} loadErrors={o.loadErrors ?? []} params={params} ports={o.serverPorts ?? ports} readOnly={o.readOnly ?? false} onClose={onClose} />
  const { rerender } = render(element(o.setups ?? [lab()]))
  return { ...spies, onClose, relist: (list: TestSetup[]) => rerender(element(list)) }
}
// The detail of the selected setup, and its controls; the dialog opens on Lab A.
const detail = () => within(screen.getByRole('region', { name: 'Lab A' }))
const btn = (name: string) => detail().getByRole('button', { name })
// The Status cell of the i-th port row of the Ports table.
const statusAt = (i: number) => within(detail().getByRole('table', { name: 'Ports' })).getAllByRole('row')[i + 1].lastElementChild as HTMLElement
const load = async () => {
  await userEvent.click(btn('Load'))
  await waitFor(() => expect(statusAt(0)).toHaveTextContent('ready'))
}

describe('SetupDialog', () => {
  it('a closed dialog keeps the Run result: reopening shows the failure and the Stop button', async () => {
    const t = open({ setups: [lab([sp(0), sp(1, { mult: '50%' })])] })
    t.start.mockImplementation(async (b) => {
      if (b.mult === '50%') throw new api.ApiError(409, 'bad_request', 'start refused')
      return { ports }
    })
    await load()
    await userEvent.click(btn('Run'))
    await waitFor(() => expect(statusAt(1)).toHaveTextContent(/^failed at start$/))
    cleanup()                                   // the page went inactive and closed the dialog
    render(<SetupDialog setups={[lab([sp(0), sp(1, { mult: '50%' })])]} loadErrors={[]} params={params} ports={ports} readOnly={false} onClose={() => undefined} />)
    expect(statusAt(1)).toHaveTextContent(/^failed at start$/)   // the reason is in the result line, once
    expect(statusAt(1)).toHaveAttribute('title', 'start refused')
    expect(btn('Stop started ports')).toBeInTheDocument()
    expect(statusAt(0)).toHaveTextContent('started')
    expect(detail().getByText('Run stopped at port 1: failed at start: start refused.')).toBeInTheDocument()
  })
  it('shows the reason for the port that lost its claim, and keeps Run disabled (the MAC-moved swap)', async () => {
    // A (key mac0, port 0) and B (key mac1, port 1); the server's port 0 now has MAC 1 and port 1 an unknown MAC
    const moved = [{ ...view(0), info: { hw_mac: mac(1) } }, { ...view(1), info: { hw_mac: 'aa:bb:cc:dd:ee:09' } }]
    open({ setups: [lab([sp(0, { key: mac(0) }), sp(1, { key: mac(1) })])], serverPorts: moved })
    await userEvent.click(btn('Load'))
    await waitFor(() => expect(statusAt(0)).toHaveTextContent('port 0 is already used by another port of this setup'))
    expect(statusAt(1)).toHaveTextContent('ready')
    expect(btn('Run')).toBeDisabled()
  })
  it('a Load that outlives a connect_seq reset writes nothing into the new session', async () => {
    open()
    let gate: () => void = () => undefined
    const opened = new Promise<void>((res) => { gate = res })
    vi.spyOn(api, 'inspect').mockImplementation(async (b) => { await opened; return inspected(b.path) })
    await userEvent.click(btn('Load'))
    setupSession.reset()                       // state.tsx does this on a new connect_seq
    gate()
    await waitFor(() => expect(btn('Load')).toBeEnabled())
    expect(setupSession.get()).toMatchObject({ setup: null, loaded: null, error: null })
  })
  it('a Run that finishes while the dialog is closed leaves its result for the next open', async () => {
    const t = open()
    let release: () => void = () => undefined
    await load()
    t.start.mockImplementation(() => new Promise((res) => { release = () => res({ ports }) }))
    await userEvent.click(btn('Run'))
    await waitFor(() => expect(t.start).toHaveBeenCalled())
    cleanup()                                   // the page went inactive; the Run goes on
    release()
    await waitFor(() => expect(runInProgress.get()).toBe(false))
    render(<SetupDialog setups={[lab()]} loadErrors={[]} params={params} ports={ports} readOnly={false} onClose={() => undefined} />)
    expect(statusAt(0)).toHaveTextContent('started')
    expect(detail().getByText(/Run finished/)).toBeInTheDocument()
  })
  it('Run is refused after a Load when the list entry keeps its slug and updated but changes a profile path', async () => {
    const t = open()
    await load()
    expect(btn('Run')).toBeEnabled()
    t.relist([lab([sp(0, { profile: { path: '/evil.py', kind: 'py' } }), sp(1)])])
    expect(btn('Run')).toBeDisabled()
    expect(detail().getByText(/changed since it was loaded; Load it again/)).toBeInTheDocument()
    t.relist([lab()])
    expect(btn('Run')).toBeEnabled()
    await userEvent.click(btn('Run'))
    await detail().findByText(/Run finished/)
    expect(t.assign.mock.calls[0][0]).toMatchObject({ path: '/p/0.py' })
  })
  it('each port row says what Run will do beyond assigning and starting', () => {
    const layer = { mode: 'L3' as const, dst_mac: null, src_ipv4: '10.0.0.1', dst_ipv4: '10.0.0.2', vlan: [], ipv6: null }
    open({ setups: [lab([sp(0, { acquire_force: true, stop_first: true, layer }), sp(1)])] })
    const [, first, second] = within(detail().getByRole('table', { name: 'Ports' })).getAllByRole('row')
    expect(within(first).getAllByRole('cell')[3]).toHaveTextContent('force-acquire, stops a transmitting port first, applies a port configuration')
    expect(within(second).getAllByRole('cell')[3]).toHaveTextContent('—')
  })
  it('says that Traffic-tab edits after Load are not used', async () => {
    open()
    await load()
    expect(detail().getByText(/Run uses the setup as saved/)).toBeInTheDocument()
  })
  it('shows each port profile path before Load, because Load runs a .py profile', () => {
    open()
    expect(detail().getByText('0.py')).toHaveAttribute('title', '/p/0.py')
    expect(statusAt(0)).toHaveTextContent('not loaded')
    expect(btn('Run')).toBeDisabled()
    expect(detail().getByText(/Load first/)).toBeInTheDocument()
  })
  it('Run stops at a foreign port and offers force-acquire with confirm', async () => {
    const t = open()
    t.acquire.mockImplementation(async (b) => {
      if (b.ports[0] === 1 && !b.force) throw new api.ApiError(409, 'not_owned', 'Port 1 is owned by bob; force-acquire to take it.')
      return { ports }
    })
    await load()
    await userEvent.click(btn('Run'))
    const force = await detail().findByRole('button', { name: 'Force-acquire port 1 and continue' })
    expect(t.start).not.toHaveBeenCalled()
    expect(t.assign).not.toHaveBeenCalled()
    await userEvent.click(force)
    // a confirm first: nothing is forced yet
    expect(t.acquire).toHaveBeenCalledTimes(2)
    await userEvent.click(btn('Force-acquire'))
    await vi.waitFor(() => expect(t.start).toHaveBeenCalledTimes(1))
    expect(t.acquire.mock.calls.map((c) => c[0])).toEqual([{ ports: [0], force: false }, { ports: [1], force: false }, { ports: [1], force: true }])
    expect(t.start).toHaveBeenCalledWith({ ports: [0, 1], mult: '10kpps', duration: -1, force: false })
    await waitFor(() => expect(statusAt(1)).toHaveTextContent('started'))
  })
  it('Run shows per-port progress and the Stop started ports button after a failure', async () => {
    const t = open({ setups: [lab([sp(0), sp(1, { mult: '50%' })])] })
    t.start.mockImplementation(async (b) => {
      if (b.mult === '50%') throw new api.ApiError(409, 'bad_request', 'start refused')
      return { ports }
    })
    await load()
    await userEvent.click(btn('Run'))
    await waitFor(() => expect(statusAt(1)).toHaveTextContent(/^failed at start$/))
    expect(statusAt(0)).toHaveTextContent('started')
    await userEvent.click(btn('Stop started ports'))
    expect(t.stop).toHaveBeenCalledWith({ ports: [0] })
    expect(btn('Run')).toBeEnabled()
  })
  it('Run is refused on a read-only session', async () => {
    const t = open({ readOnly: true })
    await load()
    expect(btn('Run')).toBeDisabled()
    expect(detail().getByText(/read-only session/)).toBeInTheDocument()
    expect(t.acquire).not.toHaveBeenCalled()
  })
  it('Run needs a Load of this version of the setup', async () => {
    open()
    await load()
    expect(btn('Run')).toBeEnabled()
  })
  it('a failed Load names the port and leaves Run disabled', async () => {
    open()
    vi.spyOn(api, 'inspect').mockRejectedValue(new api.ApiError(404, 'not_found', 'no such file'))
    await userEvent.click(btn('Load'))
    await waitFor(() => expect(statusAt(0)).toHaveTextContent('no such file'))
    expect(btn('Run')).toBeDisabled()
  })
  it('Delete confirms', async () => {
    open()
    const del = vi.spyOn(api, 'deleteSetup').mockResolvedValue({ setups: [], load_errors: [] })
    await userEvent.click(btn('Delete'))
    expect(del).not.toHaveBeenCalled()
    await userEvent.click(btn('Confirm delete'))
    expect(del).toHaveBeenCalledWith('lab-a')
  })
  it('a Rename name changed from the saved name is marked; a Duplicate name is new and is not (RC1-31)', async () => {
    open()
    await userEvent.click(btn('Rename'))
    const box = () => screen.getByRole('textbox', { name: 'New name' })
    expect(box()).not.toHaveClass('edited')
    await userEvent.type(box(), ' 2')
    expect(box()).toHaveClass('edited')
    expect(box()).toHaveAccessibleDescription('edited')
    expect(screen.getByText('1 unsaved change')).toBeInTheDocument()
    await userEvent.click(within(detail().getByRole('group', { name: 'Rename setup' })).getByRole('button', { name: 'Cancel' }))
    await userEvent.click(btn('Duplicate'))
    await userEvent.type(box(), ' 2')
    expect(box()).not.toHaveClass('edited')
    expect(screen.queryByText('edited')).not.toBeInTheDocument()
  })
  it('Rename and Duplicate send the new name', async () => {
    open()
    const rename = vi.spyOn(api, 'renameSetup').mockResolvedValue(lab())
    const dup = vi.spyOn(api, 'duplicateSetup').mockResolvedValue(lab())
    await userEvent.click(btn('Rename'))
    const form = () => within(detail().getByRole('group', { name: /setup$/ }))
    await userEvent.clear(screen.getByRole('textbox', { name: 'New name' }))
    await userEvent.type(screen.getByRole('textbox', { name: 'New name' }), 'Lab B')
    await userEvent.click(form().getByRole('button', { name: 'Rename' }))
    expect(rename).toHaveBeenCalledWith('lab-a', 'Lab B')
    await userEvent.click(btn('Duplicate'))
    await userEvent.clear(screen.getByRole('textbox', { name: 'New name' }))
    await userEvent.type(screen.getByRole('textbox', { name: 'New name' }), 'Lab C')
    await userEvent.click(form().getByRole('button', { name: 'Duplicate' }))
    expect(dup).toHaveBeenCalledWith('lab-a', 'Lab C')
  })
  it('a load_error row is disabled with its text', () => {
    open({ loadErrors: [{ file: 'broken.json', error: 'ports: must hold 1-64 entries' }] })
    const row = within(screen.getByRole('listbox', { name: 'Setups' })).getByRole('option', { name: /broken\.json/ })
    expect(row).toHaveTextContent('ports: must hold 1-64 entries')
    expect(row).toHaveAttribute('aria-disabled', 'true')
  })
  it('warns when the setup was saved for another server, and still lets Run go', async () => {
    open({ setups: [{ ...lab(), server: 'lab-a:4501' }] })
    await load()
    expect(detail().getByText(/saved for lab-a:4501/, { selector: '[role="status"]' })).toBeInTheDocument()
    expect(btn('Run')).toBeEnabled()
  })
  const second = (): TestSetup => ({ ...lab([sp(0)]), name: 'Lab B', slug: 'lab-b', server: null })
  it('the list shows server, port count, loaded and edits', async () => {
    open({ setups: [lab(), second()] })
    const list = within(screen.getByRole('listbox', { name: 'Setups' }))
    expect(list.getByRole('option', { name: /Lab A/ })).toHaveTextContent('h:4501 · 2 ports')
    expect(list.getByRole('option', { name: /Lab B/ })).toHaveTextContent('any server · 1 port')
    expect(list.getByRole('option', { name: /Lab A/ })).not.toHaveTextContent('loaded')
    await load()
    expect(list.getByRole('option', { name: /Lab A/ })).toHaveTextContent('h:4501 · 2 ports · loaded')
    act(() => trafficStore.set(0, { multValue: '50' }))
    expect(list.getByRole('option', { name: /Lab A/ })).toHaveTextContent('h:4501 · 2 ports · loaded, 1 edit')
    expect(list.getByRole('option', { name: /Lab B/ })).not.toHaveTextContent('loaded')
    await userEvent.click(list.getByRole('option', { name: /Lab B/ }))
    expect(list.getByRole('option', { name: /Lab A/ })).toHaveTextContent('loaded, 1 edit')
  })
  it('selecting another setup drops the open rename row and the last error', async () => {
    open({ setups: [lab(), second()] })
    vi.spyOn(api, 'deleteSetup').mockRejectedValue(new api.ApiError(500, 'storage_error', 'disk full'))
    await userEvent.click(btn('Rename'))
    expect(detail().getByRole('textbox', { name: 'New name' })).toHaveValue('Lab A')
    await userEvent.click(within(screen.getByRole('listbox')).getByRole('option', { name: /Lab B/ }))
    const b = within(screen.getByRole('region', { name: 'Lab B' }))
    expect(b.queryByRole('textbox', { name: 'New name' })).not.toBeInTheDocument()
    for (const n of ['Rename', 'Duplicate', 'Delete']) expect(b.getByRole('button', { name: n })).toBeEnabled()
    await userEvent.click(within(screen.getByRole('listbox')).getByRole('option', { name: /Lab A/ }))
    await userEvent.click(btn('Delete'))
    await userEvent.click(btn('Confirm delete'))
    expect(await screen.findByRole('alert')).toHaveTextContent('disk full')
    await userEvent.click(within(screen.getByRole('listbox')).getByRole('option', { name: /Lab B/ }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
  it('a Load error stays with its setup: select another and back, and it shows again', async () => {
    open({ setups: [lab(), second()] })
    vi.spyOn(api, 'inspect').mockImplementation(async (b) => { trafficStore.reset(); return inspected(b.path) })   // a new connection mid-Load
    await userEvent.click(btn('Load'))
    expect(await screen.findByRole('alert')).toHaveTextContent('The connection changed while loading')
    await userEvent.click(within(screen.getByRole('listbox')).getByRole('option', { name: /Lab B/ }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    await userEvent.click(within(screen.getByRole('listbox')).getByRole('option', { name: /Lab A/ }))
    expect(screen.getByRole('alert')).toHaveTextContent('The connection changed while loading')
  })
  it('the selection follows a renamed setup', async () => {
    const t = open({ setups: [lab(), second()] })
    const renamed = { ...second(), name: 'Lab Z', slug: 'lab-z' }
    vi.spyOn(api, 'renameSetup').mockResolvedValue(renamed)
    await userEvent.click(within(screen.getByRole('listbox')).getByRole('option', { name: /Lab B/ }))
    await userEvent.click(within(screen.getByRole('region', { name: 'Lab B' })).getByRole('button', { name: 'Rename' }))
    await userEvent.click(within(screen.getByRole('group', { name: 'Rename setup' })).getByRole('button', { name: 'Rename' }))
    t.relist([lab(), renamed])                  // the parent refreshes the list
    expect(screen.getByRole('region', { name: 'Lab Z' })).toBeInTheDocument()
  })
  it('selecting a setup shows its detail, by click and by the arrow keys', async () => {
    open({ setups: [lab(), second()] })
    const list = screen.getByRole('listbox', { name: 'Setups' })
    expect(within(list).getByRole('option', { name: /Lab A/ })).toHaveAttribute('aria-selected', 'true')
    await userEvent.click(within(list).getByRole('option', { name: /Lab B/ }))
    const b = within(screen.getByRole('region', { name: 'Lab B' }))
    expect(b.getByText(/Saved for any server · updated/)).toBeInTheDocument()
    expect(b.getAllByRole('row')).toHaveLength(2)
    expect(screen.queryByRole('region', { name: 'Lab A' })).not.toBeInTheDocument()
    within(list).getByRole('option', { name: /Lab B/ }).focus()
    await userEvent.keyboard('{ArrowUp}')
    expect(screen.getByRole('region', { name: 'Lab A' })).toBeInTheDocument()
    expect(within(list).getByRole('option', { name: /Lab A/ })).toHaveFocus()
    await userEvent.keyboard('{ArrowDown}{ArrowDown}')
    expect(within(list).getByRole('option', { name: /Lab B/ })).toHaveAttribute('aria-selected', 'true')
  })
  it('the arrow keys skip a load-error option, which is not a Tab stop', async () => {
    open({ setups: [lab()], loadErrors: [{ file: 'broken.json', error: 'bad' }] })
    const list = screen.getByRole('listbox', { name: 'Setups' })
    const bad = within(list).getByRole('option', { name: /broken\.json/ })
    expect(bad).not.toHaveAttribute('tabindex')
    within(list).getByRole('option', { name: /Lab A/ }).focus()
    await userEvent.keyboard('{ArrowDown}')
    expect(bad).toHaveAttribute('aria-selected', 'false')
    expect(within(list).getByRole('option', { name: /Lab A/ })).toHaveAttribute('aria-selected', 'true')
  })
  it('opens on the loaded setup when it is not the first', async () => {
    open({ setups: [second(), lab()] })
    expect(screen.getByRole('region', { name: 'Lab B' })).toBeInTheDocument()
    expect(within(screen.getByRole('listbox')).getByRole('option', { name: /Lab B/ })).toHaveFocus()
    await userEvent.click(within(screen.getByRole('listbox')).getByRole('option', { name: /Lab A/ }))
    await load()
    cleanup()
    render(<SetupDialog setups={[second(), lab()]} loadErrors={[]} params={params} ports={ports} readOnly={false} onClose={() => undefined} />)
    expect(screen.getByRole('region', { name: 'Lab A' })).toBeInTheDocument()
  })
  it("a failure shows in the port's Status cell and in the result line, and not as an alert", async () => {
    const t = open({ setups: [lab([sp(0), sp(1, { mult: '50%' })])] })
    t.start.mockImplementation(async (b) => {
      if (b.mult === '50%') throw new api.ApiError(409, 'bad_request', 'start refused')
      return { ports }
    })
    await load()
    await userEvent.click(btn('Run'))
    await waitFor(() => expect(statusAt(1)).toHaveTextContent(/^failed at start$/))
    expect(screen.getAllByText(/start refused/)).toHaveLength(1)   // the result line only: the Status cell names the step
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(detail().getByText('Run stopped at port 1: failed at start: start refused.')).toBeInTheDocument()
  })
  it("a failed Run scrolls the failed port's row into view, above the sticky bar", async () => {
    const scroll = vi.fn()
    Element.prototype.scrollIntoView = scroll   // jsdom has none
    try {
      const t = open({ setups: [lab([sp(0), sp(1, { mult: '50%' })])] })
      t.start.mockImplementation(async (b) => {
        if (b.mult === '50%') throw new api.ApiError(409, 'bad_request', 'start refused')
        return { ports }
      })
      await load()
      expect(scroll).not.toHaveBeenCalled()
      await userEvent.click(btn('Run'))
      await waitFor(() => expect(scroll).toHaveBeenCalledWith({ block: 'nearest' }))
      const row = within(detail().getByRole('table', { name: 'Ports' })).getAllByRole('row')[2]
      expect(scroll.mock.contexts).toEqual([row])
    } finally { delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView }
  })
  it('a reason that already ends in a period is not given a second one', async () => {
    const t = open({ setups: [lab([sp(0), sp(1, { mult: '50%' })])] })
    t.start.mockImplementation(async (b) => {
      if (b.mult === '50%') throw new api.ApiError(409, 'bad_request', 'Port 1 is busy.')
      return { ports }
    })
    await load()
    await userEvent.click(btn('Run'))
    expect(await detail().findByText('Run stopped at port 1: failed at start: Port 1 is busy.')).toBeInTheDocument()
  })
  it('the empty state names the Control page', () => {
    open({ setups: [] })
    expect(screen.getByText('No saved setups. Use Setups ▾ → Save current… on the Control page.')).toBeInTheDocument()
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument()
  })
  it('disconnected greys Load and Run with Connect first', () => {
    vi.spyOn(api, 'inspect').mockImplementation(async (b) => inspected(b.path))
    render(<SetupDialog setups={[lab()]} loadErrors={[]} params={null} ports={[]} readOnly={false} onClose={() => undefined} />)
    expect(btn('Load')).toBeDisabled()
    expect(btn('Run')).toBeDisabled()
    expect(detail().getByText('Connect first')).toHaveClass('muted')
  })
  it('Close sits in the footer end', async () => {
    const t = open()
    const close = screen.getByRole('button', { name: 'Close' })
    expect(close.closest('.dialog-footer .end')).not.toBeNull()
    await userEvent.click(close)
    expect(t.onClose).toHaveBeenCalled()
  })
  describe('edits made in the Traffic tab after Load', () => {
    const edit = (port: number, patch: Parameters<typeof trafficStore.set>[1]) => act(() => trafficStore.set(port, patch))
    const saveRun = () => btn('Save and Run')
    const layer = { mode: 'L3' as const, dst_mac: null, src_ipv4: '10.0.0.1', dst_ipv4: '10.0.0.2', vlan: [], ipv6: null }
    const dashboard = { tab: 'latency' as const, window_s: 120, layout: 2, streams_pgids: [1], latency_pgids: [2] }
    const setup2 = () => ({ ...lab([sp(0, { stop_first: true, acquire_force: true, layer }), sp(1)]), dashboard })
    it('names each changed field with old and new values, and offers Save and Run beside Run as saved', async () => {
      open({ setups: [setup2()] })
      await load()
      expect(detail().queryByRole('button', { name: 'Save and Run' })).not.toBeInTheDocument()
      expect(btn('Run')).toBeEnabled()
      edit(0, { multValue: '50', duration: '30' })
      expect(within(screen.getByRole('status')).getByText('Port 0: rate 10 kpps → 50 kpps, duration no limit → 30 s — not used by Run')).toBeInTheDocument()
      expect(screen.queryByText(/after Load are not used/)).not.toBeInTheDocument()
      expect(btn('Run as saved')).toBeEnabled()
      expect(saveRun()).toBeEnabled()
      edit(0, { multValue: '10', duration: '' })
      expect(detail().queryByRole('button', { name: 'Save and Run' })).not.toBeInTheDocument()
    })
    it('Save and Run saves the new rate under the same slug, inspects again, then runs with the new rate', async () => {
      const t = open({ setups: [setup2()] })
      const order: string[] = []
      const inspectSpy = vi.mocked(api.inspect)
      inspectSpy.mockImplementation(async (b) => { order.push('inspect'); return inspected(b.path) })
      const put = vi.spyOn(api, 'putSetup').mockImplementation(async (_slug, body) => { order.push('put'); return { ...body, updated: 'u2' } })
      t.acquire.mockImplementation(async () => { order.push('acquire'); return { ports } })
      await load()
      order.length = 0
      edit(0, { multValue: '50', unit: 'kpps' })
      await userEvent.click(saveRun())
      await detail().findByText(/Run finished/)
      expect(put).toHaveBeenCalledTimes(1)
      expect(put.mock.calls[0][0]).toBe('lab-a')
      const body = put.mock.calls[0][1]
      expect(body.ports.map((p) => p.mult)).toEqual(['50kpps', '10kpps'])
      expect(body.ports[0]).toMatchObject({ key: mac(0), stop_first: true, acquire_force: true, layer })
      expect(body.dashboard).toEqual(dashboard)
      expect(body).toMatchObject({ name: 'Lab A', slug: 'lab-a', server: 'h:4501' })
      expect(order.slice(0, 5)).toEqual(['put', 'inspect', 'inspect', 'inspect', 'inspect'])
      expect(order.indexOf('acquire')).toBeGreaterThan(order.lastIndexOf('inspect'))
      expect(t.start).toHaveBeenCalledWith({ ports: [0], mult: '50kpps', duration: -1, force: false })
      expect(t.acquire.mock.calls[0][0]).toEqual({ ports: [0], force: true })
      expect(detail().queryByRole('button', { name: 'Save and Run' })).not.toBeInTheDocument()
    })
    it('an invalid form disables Save and Run and says why; a save that fails sends no acquire', async () => {
      const t = open({ setups: [setup2()] })
      await load()
      edit(1, { multValue: '0' })
      expect(saveRun()).toBeDisabled()
      expect(detail().getByText(/fix the form first: Port 1: multiplier is not valid/)).toBeInTheDocument()
      edit(1, { multValue: '5' })
      const put = vi.spyOn(api, 'putSetup').mockRejectedValue(new api.ApiError(500, 'storage_error', 'disk full'))
      await userEvent.click(saveRun())
      expect(await screen.findByRole('alert')).toHaveTextContent('disk full')
      expect(put).toHaveBeenCalledTimes(1)
      expect(t.acquire).not.toHaveBeenCalled()
      expect(t.start).not.toHaveBeenCalled()
    })
    it('a failed save leaves the previous Load status and Run result in place', async () => {
      open({ setups: [setup2()] })
      await load()
      await userEvent.click(btn('Run'))
      await detail().findByText(/Run finished/)
      edit(0, { multValue: '50' })
      vi.spyOn(api, 'putSetup').mockRejectedValue(new api.ApiError(500, 'storage_error', 'disk full'))
      await userEvent.click(saveRun())
      await screen.findByText('disk full')
      expect(statusAt(0)).toHaveTextContent('started')
      expect(detail().getByText(/Run finished/)).toBeInTheDocument()
    })
    it('a re-Load that fails sends no acquire', async () => {
      const t = open({ setups: [setup2()] })
      await load()
      edit(0, { multValue: '50' })
      vi.spyOn(api, 'putSetup').mockImplementation(async (_s, body) => body)
      vi.mocked(api.inspect).mockRejectedValue(new api.ApiError(404, 'not_found', 'no such file'))
      await userEvent.click(saveRun())
      await waitFor(() => expect(statusAt(0)).toHaveTextContent('no such file'))
      expect(t.acquire).not.toHaveBeenCalled()
    })
    it('Run refuses when another client changed the setup during the re-Load', async () => {
      const t = open({ setups: [setup2()] })
      await load()
      edit(0, { multValue: '50' })
      vi.spyOn(api, 'putSetup').mockImplementation(async (_s, body) => body)
      const changed = { ...setup2(), ports: [sp(0, { mult: '1' }), sp(1)] }
      vi.mocked(api.inspect).mockImplementation(async (b) => { t.relist([changed]); return inspected(b.path) })
      await userEvent.click(saveRun())
      expect(await screen.findByRole('alert')).toHaveTextContent(/changed since it was loaded/)
      expect(t.acquire).not.toHaveBeenCalled()
    })
    it('is disabled on a read-only session', async () => {
      open({ readOnly: true })
      await load()
      edit(0, { multValue: '50' })
      expect(saveRun()).toBeDisabled()
    })
  })
})
