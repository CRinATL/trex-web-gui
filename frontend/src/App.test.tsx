import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from './api'
import * as state from './state'
import * as ws from './ws'
import { AppShell } from './App'
import { wirePort } from './testWire'

function Where() { return <span data-testid="where">{useLocation().pathname}</span> }

afterEach(() => vi.restoreAllMocks())

describe('shell', () => {
  it('shows the not-authorised page on 401', async () => {
    // a real provider: boot asks /api/auth/status, which answers false for a browser without the cookie
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, statusText: 'OK', json: async () => ({ authenticated: false }) })))
    vi.stubGlobal('WebSocket', class { onopen?: () => void; onclose?: () => void; close() { /* never opens */ } })
    try {
      render(<state.AppStateProvider><MemoryRouter initialEntries={['/control']}><AppShell /></MemoryRouter></state.AppStateProvider>)
      expect(await screen.findByRole('alert')).toHaveTextContent(/Not authorised/)
      expect(screen.getByRole('alert')).toHaveTextContent(/Open the URL printed by trex-web-gui serve on the backend host; through a tunnel, the same URL with localhost\./)
      expect(screen.queryByRole('link', { name: 'Control' })).toBeNull()      // the shell is not rendered
    } finally { vi.unstubAllGlobals() }
  })
  it('the nav is Control, Dashboard, Capture, Studio, Monitor, Events, Settings', async () => {
    render(<MemoryRouter initialEntries={['/monitor']}><AppShell /></MemoryRouter>)
    expect(within(screen.getByRole('navigation')).getAllByRole('link').map((a) => a.textContent)).toEqual(['Control', 'Dashboard', 'Capture', 'Studio', 'Monitor', 'Events', 'Settings'])
    expect(screen.getByRole('heading', { name: 'Monitor' })).toBeInTheDocument()   // routes stay reachable by URL
  })
  it('the top bar links to Studio, between Capture and Monitor', async () => {
    vi.spyOn(api, 'listLibrary').mockResolvedValue({ profiles: [], load_errors: [], dir: '/lib' })
    render(<MemoryRouter initialEntries={['/dashboard']}><AppShell /><Where /></MemoryRouter>)
    const links = within(screen.getByRole('navigation')).getAllByRole('link').map((a) => a.textContent)
    expect(links.indexOf('Studio')).toBe(links.indexOf('Capture') + 1)
    expect(links.indexOf('Monitor')).toBe(links.indexOf('Studio') + 1)
    await userEvent.click(screen.getByRole('link', { name: 'Studio' }))
    expect(screen.getByTestId('where')).toHaveTextContent('/studio')
    expect(screen.getByRole('heading', { name: 'Studio' })).toBeVisible()
  })
  it('top bar links to Monitor', async () => {
    render(<MemoryRouter initialEntries={['/dashboard']}><AppShell /><Where /></MemoryRouter>)
    await userEvent.click(screen.getByRole('link', { name: 'Monitor' }))
    expect(screen.getByTestId('where')).toHaveTextContent('/monitor')
    expect(screen.getByRole('heading', { name: 'Monitor', level: 1 })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Connect' })).toBeInTheDocument()
  })
  describe('monitor-only', () => {
    const only = () => vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, helloSeen: true, backendOnline: true, monitorOnly: true, monitorTarget: { host: 'lab1', port: 4500 } })
    it('monitor-only shell shows only Monitor and Settings', async () => {
      const fetchMock = vi.fn(async () => ({ ok: true, status: 200, statusText: 'OK', json: async () => ({}) }))
      vi.stubGlobal('fetch', fetchMock)
      try {
        only()
        render(<MemoryRouter initialEntries={['/control']}><AppShell /><Where /></MemoryRouter>)
        expect(screen.getByTestId('where')).toHaveTextContent('/monitor')                 // /control lands on Monitor
        expect(within(screen.getByRole('navigation')).getAllByRole('link').map((a) => a.textContent)).toEqual(['Monitor', 'Settings'])
        expect(screen.getByRole('heading', { name: 'Monitor', level: 1 })).toBeVisible()
        expect(screen.queryByRole('button', { name: /^Connect…/ })).toBeNull()             // no Control connect in the status bar
        const key = new KeyboardEvent('keydown', { key: 'c', ctrlKey: true, shiftKey: true, bubbles: true, cancelable: true })
        document.body.dispatchEvent(key)
        expect(key.defaultPrevented).toBe(false)                                           // ctrl+shift+c is not bound (a bound hotkey prevents the default)
        expect(screen.queryByRole('dialog')).toBeNull()
        await userEvent.click(screen.getByRole('link', { name: 'Settings' }))
        expect(screen.getByRole('radio', { name: 'System' })).toBeInTheDocument()          // the Theme section
        expect(screen.queryByRole('tab')).toBeNull()                                       // no Files, Session, Backup or About
        const urls = fetchMock.mock.calls.map((c) => String((c as unknown[])[0]))
        expect(urls.filter((u) => u.startsWith('/api/settings'))).toEqual([])              // Theme needs no backend
      } finally { vi.unstubAllGlobals() }
    })
    it('monitor-only makes no /api/capture request', async () => {
      // the backend answers every /api/capture route 409 in this mode, and the Capture page asks for its file list on mount
      const fetchMock = vi.fn(async () => ({ ok: true, status: 200, statusText: 'OK', json: async () => ({}) }))
      vi.stubGlobal('fetch', fetchMock)
      try {
        only()
        for (const path of ['/capture', '/control', '/monitor']) {
          const { unmount } = render(<MemoryRouter initialEntries={[path]}><AppShell /></MemoryRouter>)
          await act(async () => { await Promise.resolve() })
          unmount()
        }
        expect(fetchMock.mock.calls.map((c) => String((c as unknown[])[0])).filter((u) => u.startsWith('/api/capture'))).toEqual([])
      } finally { vi.unstubAllGlobals() }
    })
    it('before hello nothing is mounted and no capture or studio request is made, whichever way hello then goes', async () => {
      const fetchMock = vi.fn(async (url: unknown) => ({ ok: true, status: 200, statusText: 'OK', json: async () => (String(url).startsWith('/api/auth/status') ? { authenticated: true } : {}) }))
      vi.stubGlobal('fetch', fetchMock)
      let onMsg: (m: unknown) => void = () => undefined
      vi.spyOn(ws, 'connectSocket').mockImplementation((_url, m) => { onMsg = m; return () => undefined })
      const urls = () => fetchMock.mock.calls.map((c) => String((c as unknown[])[0]))
      const refused = () => urls().filter((u) => u.startsWith('/api/capture') || u.startsWith('/api/studio'))
      try {
        render(<state.AppStateProvider><MemoryRouter initialEntries={['/capture']}><AppShell /><Where /></MemoryRouter></state.AppStateProvider>)
        await act(async () => { await Promise.resolve() })
        expect(screen.getByRole('status')).toHaveTextContent('Connecting…')
        expect(screen.queryByRole('navigation')).toBeNull()
        expect(screen.queryByRole('heading')).toBeNull()
        expect(refused()).toEqual([])
        await act(async () => { onMsg({ type: 'hello', server_hint: null, server_ports: [], monitor_only: true, monitor_target: { host: 'lab1', port: 4500 } }); await Promise.resolve() })
        expect(screen.getByTestId('where')).toHaveTextContent('/monitor')
        expect(within(screen.getByRole('navigation')).getAllByRole('link').map((a) => a.textContent)).toEqual(['Monitor', 'Settings'])
        await act(async () => { await Promise.resolve() })
        expect(refused()).toEqual([])
      } finally { vi.unstubAllGlobals() }
    })
    it('the full shell does ask for the capture files (the check above can fail)', async () => {
      const files = vi.spyOn(api, 'captureFiles').mockResolvedValue({ files: [], total: 0, bytes: 0 } as unknown as Awaited<ReturnType<typeof api.captureFiles>>)
      render(<MemoryRouter initialEntries={['/control']}><AppShell /></MemoryRouter>)
      await act(async () => { await Promise.resolve() })
      expect(files).toHaveBeenCalled()
    })
    it.each(['/dashboard', '/capture', '/studio', '/events', '/'])('%s redirects to the Monitor', (path) => {
      only()
      render(<MemoryRouter initialEntries={[path]}><AppShell /><Where /></MemoryRouter>)
      expect(screen.getByTestId('where')).toHaveTextContent('/monitor')
    })
    it('the status bar shows the Monitor LED line', () => {
      only()
      render(<MemoryRouter initialEntries={['/monitor']}><AppShell /></MemoryRouter>)
      expect(within(screen.getByRole('contentinfo')).getByRole('status')).toHaveTextContent('Disconnected')
    })
  })
  it('Studio keeps its selection across a page switch', async () => {
    const e = (slug: string) => ({ slug, name: slug, path: `/lib/${slug}.json`, stream_count: 1, created: '', updated: '', source: null, source_missing: false, load_error: null })
    vi.spyOn(api, 'listLibrary').mockResolvedValue({ profiles: [e('a'), e('b')], load_errors: [], dir: '/lib' })
    render(<MemoryRouter initialEntries={['/studio']}><AppShell /></MemoryRouter>)
    await userEvent.click(await screen.findByRole('option', { name: /^b/ }))
    expect(screen.getByRole('option', { name: /^b/ })).toHaveAttribute('aria-selected', 'true')
    await userEvent.click(screen.getByRole('link', { name: 'Dashboard' }))
    expect(screen.queryByRole('listbox', { name: 'Profiles' })).toBeNull()    // hidden elsewhere
    await userEvent.click(screen.getByRole('link', { name: 'Studio' }))
    expect(await screen.findByRole('option', { name: /^b/ })).toHaveAttribute('aria-selected', 'true')
  })
  it("Studio's packet editor route shows the page and keeps the Studio link active", async () => {
    vi.spyOn(api, 'listLibrary').mockResolvedValue({ profiles: [], load_errors: [], dir: '/lib' })
    const defs = vi.spyOn(api, 'packetDefinitions').mockRejectedValue(new Error('no backend here'))
    vi.spyOn(api, 'buildPacket').mockRejectedValue(new Error('no backend here'))
    render(<MemoryRouter initialEntries={['/studio']}><AppShell /><Where /></MemoryRouter>)
    expect(defs).not.toHaveBeenCalled()                                         // the editor is not mounted before its first visit
    await userEvent.click(screen.getByRole('link', { name: 'Packet editor' }))
    expect(screen.getByTestId('where')).toHaveTextContent('/studio/packet')
    expect(screen.getByRole('heading', { name: /^Packet editor/, level: 1 })).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Studio', hidden: true })).not.toBeVisible()  // the library page is hidden
    expect(screen.getByRole('link', { name: 'Studio' })).toHaveAttribute('aria-current', 'page')
    // away and back: the same page, not a new one (it is mounted once and hidden)
    await userEvent.click(screen.getByRole('link', { name: 'Dashboard' }))
    expect(screen.getByRole('heading', { name: /^Packet editor/, level: 1, hidden: true })).not.toBeVisible()
    expect(defs).toHaveBeenCalledTimes(1)
    await userEvent.click(screen.getByRole('link', { name: 'Studio' }))
    expect(screen.getByRole('heading', { name: 'Studio' })).toBeVisible()
    expect(screen.getByRole('heading', { name: /^Packet editor/, level: 1, hidden: true })).not.toBeVisible()
  })
  it('the Events link shows the unseen count', () => {
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, helloSeen: true, backendOnline: true, logUnseen: 3 })
    render(<MemoryRouter initialEntries={['/control']}><AppShell /></MemoryRouter>)
    expect(screen.getByLabelText('3 unseen errors and warnings')).toHaveTextContent('3')
    expect(screen.getByRole('link', { name: /^Events/ })).toContainElement(screen.getByLabelText('3 unseen errors and warnings'))
  })
  it('the Events link has no badge when nothing is unseen', () => {
    render(<MemoryRouter initialEntries={['/control']}><AppShell /></MemoryRouter>)
    expect(screen.queryByLabelText(/unseen errors and warnings/)).toBeNull()
  })
  it('has a Capture entry after Dashboard, and the page is mounted hidden elsewhere and shown at /capture', async () => {
    render(<MemoryRouter initialEntries={['/dashboard']}><AppShell /></MemoryRouter>)
    const links = within(screen.getByRole('navigation')).getAllByRole('link').map((a) => a.textContent)
    expect(links.indexOf('Capture')).toBe(links.indexOf('Dashboard') + 1)
    expect(screen.queryByRole('heading', { name: 'Capture' })).toBeNull()   // mounted, but hidden
    await userEvent.click(screen.getByRole('link', { name: 'Capture' }))
    expect(screen.getByRole('heading', { name: 'Capture' })).toBeVisible()
  })
  it('shows the Control page at /control/ with a trailing slash', () => {
    render(<MemoryRouter initialEntries={['/control/']}><AppShell /></MemoryRouter>)
    expect(screen.getByRole('heading', { name: 'Control' })).toBeInTheDocument()
  })
  it('redirects / to the Control workspace', () => {
    render(<MemoryRouter initialEntries={['/']}><AppShell /></MemoryRouter>)
    expect(screen.getByRole('heading', { name: 'Control' })).toBeInTheDocument()
  })
  it('selection and tunables survive visiting Settings and coming back', async () => {
    vi.spyOn(api, 'getSettings').mockResolvedValue({ settings: { preferences: { templates_dir: '', wireshark_path: 'wireshark', workspace_dir: '', release_on_exit: true, trex_cfg_path: '/etc/trex_cfg.yaml' }, connections: [] }, load_error: null })
    const session = { type: 'session' as const, state: 'connected' as const, params: null, info: null, error: null, rpc_console: false, include_polling: false }
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, helloSeen: true, backendOnline: true, session, ports: [wirePort('streams_owned')] })
    render(<MemoryRouter initialEntries={['/control']}><AppShell /><Where /></MemoryRouter>)
    await userEvent.click(screen.getByRole('treeitem', { name: /^Port 0,/ }))
    await userEvent.click(screen.getByRole('tab', { name: 'Traffic' }))
    await userEvent.clear(screen.getByLabelText('Multiplier'))
    await userEvent.type(screen.getByLabelText('Multiplier'), '37')
    await userEvent.click(screen.getByRole('link', { name: 'Settings' }))
    expect(screen.getByTestId('where')).toHaveTextContent('/settings')
    expect(screen.queryByRole('textbox', { name: 'Multiplier' })).toBeNull()   // hidden subtree: ByRole queries skip it
    await userEvent.click(screen.getByRole('link', { name: 'Control' }))
    expect(screen.getByRole('treeitem', { name: /^Port 0,/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'Traffic' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByLabelText('Multiplier')).toHaveValue('37')
  })
  it('an open start-all dialog is gone after navigating to Settings', async () => {
    vi.spyOn(api, 'getSettings').mockResolvedValue({ settings: { preferences: { templates_dir: '', wireshark_path: 'wireshark', workspace_dir: '', release_on_exit: true, trex_cfg_path: '/etc/trex_cfg.yaml' }, connections: [] }, load_error: null })
    const session = { type: 'session' as const, state: 'connected' as const, params: null, info: null, error: null, rpc_console: false, include_polling: false }
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, helloSeen: true, backendOnline: true, session, ports: [wirePort('streams_owned')] })
    render(<MemoryRouter initialEntries={['/control']}><AppShell /></MemoryRouter>)
    await userEvent.click(screen.getByRole('button', { name: 'Start all' }))
    expect(screen.getByRole('dialog', { name: 'Start all ports' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('link', { name: 'Settings' }))
    expect(document.querySelector('[role="dialog"]')).toBeNull()
  })
  it('the Dashboard link navigates', async () => {
    render(<MemoryRouter initialEntries={['/control']}><AppShell /><Where /></MemoryRouter>)
    await userEvent.click(screen.getByRole('link', { name: 'Dashboard' }))
    expect(screen.getByTestId('where')).toHaveTextContent('/dashboard')
    expect(screen.getByRole('heading', { name: 'Dashboard' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Control' })).toBeNull()
  })
  it('navigating moves focus to <main>', async () => {
    render(<MemoryRouter initialEntries={['/control']}><AppShell /></MemoryRouter>)
    expect(screen.getByRole('main')).not.toHaveFocus()
    const link = screen.getByRole('link', { name: 'Dashboard' })
    link.focus()
    await userEvent.click(link)
    expect(screen.getByRole('main')).toHaveFocus()
    await userEvent.click(screen.getByRole('link', { name: 'Settings' }))
    expect(screen.getByRole('main')).toHaveFocus()
  })
  it.each(['/settings', '/studio', '/control'])('ctrl+shift+c from %s opens the connect dialog over the same page', async (path) => {
    vi.spyOn(api, 'getSettings').mockResolvedValue({ settings: { preferences: { templates_dir: '', wireshark_path: 'wireshark', workspace_dir: '', release_on_exit: true, trex_cfg_path: '/etc/trex_cfg.yaml' }, connections: [] }, load_error: null })
    render(<MemoryRouter initialEntries={[path]}><AppShell /><Where /></MemoryRouter>)
    await userEvent.keyboard('c')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await userEvent.keyboard('{Control>}{Shift>}c{/Shift}{/Control}')
    expect(await screen.findByRole('dialog', { name: 'Connect to TRex' })).toBeInTheDocument()
    expect(screen.getByTestId('where')).toHaveTextContent(path)
  })
  it('ctrl+shift+p opens Settings', async () => {
    vi.spyOn(api, 'getSettings').mockResolvedValue({ settings: { preferences: { templates_dir: '', wireshark_path: 'wireshark', workspace_dir: '', release_on_exit: true, trex_cfg_path: '/etc/trex_cfg.yaml' }, connections: [] }, load_error: null })
    render(<MemoryRouter initialEntries={['/control']}><AppShell /></MemoryRouter>)
    await userEvent.keyboard('{Control>}{Shift>}p{/Shift}{/Control}')
    expect(await screen.findByRole('heading', { name: 'Settings' })).toBeInTheDocument()
  })
  it('Connect… in the status bar opens the dialog', async () => {
    vi.spyOn(api, 'getSettings').mockResolvedValue({ settings: { preferences: { templates_dir: '', wireshark_path: 'wireshark', workspace_dir: '', release_on_exit: true, trex_cfg_path: '/etc/trex_cfg.yaml' }, connections: [] }, load_error: null })
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, helloSeen: true, backendOnline: true })
    render(<MemoryRouter initialEntries={['/control']}><AppShell /></MemoryRouter>)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await userEvent.click(within(screen.getByRole('contentinfo')).getByRole('button', { name: 'Connect…' }))
    expect(await screen.findByRole('dialog', { name: 'Connect to TRex' })).toBeInTheDocument()
  })
  it('Connect… after a loss prefills the dialog with the whole lost session params', async () => {
    vi.spyOn(api, 'getSettings').mockResolvedValue({ settings: { preferences: { templates_dir: '', wireshark_path: 'wireshark', workspace_dir: '', release_on_exit: true, trex_cfg_path: '/etc/trex_cfg.yaml' }, connections: [] }, load_error: null })
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, helloSeen: true, backendOnline: true,
      session: { type: 'session', state: 'lost', params: { host: 'lab9', sync_port: 5501, async_port: 5500, timeout_s: 7 }, info: null, error: 'subscriber timeout', rpc_console: false, include_polling: false } })
    render(<MemoryRouter initialEntries={['/control']}><AppShell /></MemoryRouter>)
    await userEvent.click(within(screen.getByRole('contentinfo')).getByRole('button', { name: 'Connect…' }))
    const dlg = await screen.findByRole('dialog', { name: 'Connect to TRex' })
    expect(within(dlg).getByLabelText('Host')).toHaveValue('lab9')
    expect(within(dlg).getByLabelText('Command port')).toHaveValue('5501')
    expect(within(dlg).getByLabelText('Event port')).toHaveValue('5500')
    expect(within(dlg).getByLabelText('Event port')).toBeVisible()   // not the default, so Advanced is open
    expect(within(dlg).getByLabelText('Timeout (s)')).toHaveValue('7')
  })
})
