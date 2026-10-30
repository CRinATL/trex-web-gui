import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import type { MonitorMsg } from '../api'
import { monitorStore } from '../monitorStore'
import stl from '../__fixtures__/monitorStl.json'
import stateful from '../__fixtures__/monitorStateful.json'
import { MonitorPage } from './MonitorPage'
import { ALREADY } from '../components/monitor/MonitorConnect'

const base = stl as unknown as MonitorMsg
const push = (over: Partial<MonitorMsg> = {}) => act(() => monitorStore.push({ ...base, ...over }))

beforeEach(() => localStorage.clear())
afterEach(() => { vi.restoreAllMocks(); localStorage.clear() })

describe('MonitorPage', () => {
  it('connect posts host and port and remembers the host', async () => {
    const connect = vi.spyOn(api, 'monitorConnect').mockResolvedValue(base)
    render(<MonitorPage />)
    await userEvent.clear(screen.getByLabelText(/^Host/))
    await userEvent.type(screen.getByLabelText(/^Host/), 'lab1')
    expect(screen.getByLabelText(/^Port/)).toHaveValue('4500')
    await userEvent.click(screen.getByRole('button', { name: 'Connect' }))
    expect(connect).toHaveBeenCalledWith('lab1', 4500)
    await vi.waitFor(() => expect(JSON.parse(localStorage.getItem('trex-web-gui.monitorHosts') ?? '[]')).toEqual([{ host: 'lab1', port: 4500 }]))
  })

  it('does not remember a host the backend refused, and shows why', async () => {
    vi.spyOn(api, 'monitorConnect').mockRejectedValue(new api.ApiError(422, 'invalid', 'bad endpoint'))
    render(<MonitorPage />)
    await userEvent.click(screen.getByRole('button', { name: 'Connect' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('bad endpoint')
    expect(localStorage.getItem('trex-web-gui.monitorHosts')).toBeNull()
  })

  it('Disconnect posts the disconnect', async () => {
    const off = vi.spyOn(api, 'monitorDisconnect').mockResolvedValue({ ...base, state: 'disconnected' })
    render(<MonitorPage />)
    push({ state: 'connected', target: { host: 'gen-1', port: 4500 } })
    await userEvent.click(screen.getByRole('button', { name: 'Disconnect' }))
    expect(off).toHaveBeenCalledTimes(1)
  })

  it('Connect is off while connected to the publisher in the fields, on for another one; Disconnect needs a target', async () => {
    render(<MonitorPage />)
    push({ state: 'disconnected', target: null })
    expect(screen.getByRole('button', { name: 'Disconnect' })).toBeDisabled()
    push({ state: 'connected', target: { host: '127.0.0.1', port: 4500 } })
    const connect = screen.getByRole('button', { name: 'Connect' })
    await userEvent.clear(screen.getByLabelText(/^Host/))
    await userEvent.type(screen.getByLabelText(/^Host/), '127.0.0.1')
    expect(connect).toBeDisabled()
    expect(connect).toHaveAttribute('title', ALREADY)
    const portField = screen.getByRole('textbox', { name: 'Port' })
    await userEvent.clear(portField)
    await userEvent.type(portField, '4600')
    expect(connect).toBeEnabled()
    push({ state: 'stale', target: { host: '127.0.0.1', port: 4600 } })
    expect(connect).toBeEnabled()
    push({ state: 'connecting', target: { host: '127.0.0.1', port: 4600 } })
    expect(connect).toBeDisabled()
  })

  it('history lists recent hosts first', async () => {
    localStorage.setItem('trex-web-gui.monitorHosts', JSON.stringify([{ host: 'b', port: 4500 }, { host: 'a', port: 4501 }]))
    vi.spyOn(api, 'monitorConnect').mockResolvedValue(base)
    const { container } = render(<MonitorPage />)
    const options = () => [...container.querySelectorAll('datalist option')].map((o) => o.getAttribute('value'))
    expect(options()).toEqual(['b:4500', 'a:4501'])
    await userEvent.clear(screen.getByLabelText(/^Host/))
    await userEvent.type(screen.getByLabelText(/^Host/), 'a')
    await userEvent.clear(screen.getByLabelText(/^Port/))
    await userEvent.type(screen.getByLabelText(/^Port/), '4501')
    await userEvent.click(screen.getByRole('button', { name: 'Connect' }))
    await vi.waitFor(() => expect(options()).toEqual(['a:4501', 'b:4500']))
  })

  it('keeps ten entries at most, and survives broken storage', async () => {
    localStorage.setItem('trex-web-gui.monitorHosts', JSON.stringify(Array.from({ length: 10 }, (_, i) => ({ host: `h${i}`, port: 4500 }))))
    vi.spyOn(api, 'monitorConnect').mockResolvedValue(base)
    render(<MonitorPage />)
    await userEvent.click(screen.getByRole('button', { name: 'Connect' }))
    await vi.waitFor(() => expect(JSON.parse(localStorage.getItem('trex-web-gui.monitorHosts') ?? '[]')).toHaveLength(10))
    localStorage.setItem('trex-web-gui.monitorHosts', '{not json')
    const { container } = render(<MonitorPage />)
    expect(container.querySelectorAll('datalist option')).toHaveLength(0)
  })

  it('selecting a remembered entry fills the host and the port', async () => {
    localStorage.setItem('trex-web-gui.monitorHosts', JSON.stringify([{ host: 'lab9', port: 4510 }]))
    render(<MonitorPage />)
    await userEvent.clear(screen.getByLabelText(/^Host/))
    await userEvent.paste('lab9:4510')   // what a datalist pick types into the field
    expect(screen.getByLabelText(/^Host/)).toHaveValue('lab9')
    expect(screen.getByLabelText(/^Port/)).toHaveValue('4510')
  })

  it('an IPv6 host is remembered and picked back as host and port', async () => {
    const connect = vi.spyOn(api, 'monitorConnect').mockResolvedValue(base)
    const { container } = render(<MonitorPage />)
    await userEvent.clear(screen.getByLabelText(/^Host/))
    await userEvent.type(screen.getByLabelText(/^Host/), '::1')
    await userEvent.click(screen.getByRole('button', { name: 'Connect' }))
    await vi.waitFor(() => expect(JSON.parse(localStorage.getItem('trex-web-gui.monitorHosts') ?? '[]')).toEqual([{ host: '::1', port: 4500 }]))
    const pick = container.querySelector('datalist option')?.getAttribute('value') ?? ''
    await userEvent.clear(screen.getByLabelText(/^Host/))
    await userEvent.paste(pick)
    expect(screen.getByLabelText(/^Host/)).toHaveValue('::1')
    expect(screen.getByLabelText(/^Port/)).toHaveValue('4500')
    connect.mockClear()
    await userEvent.click(screen.getByRole('button', { name: 'Connect' }))
    expect(connect).toHaveBeenCalledWith('::1', 4500)
  })

  it('old string entries in storage are ignored', () => {
    localStorage.setItem('trex-web-gui.monitorHosts', JSON.stringify(['lab9:4510', { host: 'ok', port: 1 }]))
    const { container } = render(<MonitorPage />)
    expect([...container.querySelectorAll('datalist option')].map((o) => o.getAttribute('value'))).toEqual(['ok:1'])
  })

  it('panels hide until their message arrives', () => {
    render(<MonitorPage />)
    expect(screen.queryByRole('heading', { name: /Global/ })).toBeNull()
    expect(screen.queryByRole('heading', { name: /Ports/ })).toBeNull()
    push()   // the recorded STL message: trex-global only, every other name still null (the Inspector lists the names, so it shows)
    expect(screen.getByRole('heading', { name: /Global/ })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /Ports/ })).toBeInTheDocument()
    expect(screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent?.replace(/[▸▾]\s*/, ''))).toEqual(['Global', 'Ports', 'Inspector'])
  })

  it('the stateful message shows every panel', () => {
    render(<MonitorPage />)
    act(() => monitorStore.push(stateful as unknown as MonitorMsg))
    expect(screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent?.replace(/[▸▾]\s*/, '')))
      .toEqual(['Global', 'Ports', 'Templates', 'Latency', 'Rx-check', 'Latency v2', 'Inspector'])
  })

  it("names its own source, outside the live region, so it is not read as the Control session's", () => {
    render(<MonitorPage />)
    push({ target: { host: 'gen-1', port: 4500 } })
    expect(screen.getByText('Monitor source gen-1:4500')).toBeInTheDocument()
    expect(screen.getByRole('status')).not.toHaveTextContent('gen-1')
  })

  it('shows the status line with the LED text, counts and drops', () => {
    render(<MonitorPage />)
    expect(screen.getByRole('status')).toHaveTextContent('Disconnected')
    push({ frames: 1234, bytes: 56789, dropped: { oversize: 2, undecodable: 0, other_names: 0, long_name: 0 } })
    expect(screen.getByRole('status')).toHaveTextContent('Connected')
    expect(screen.getByText('1,234 frames · 56,789 bytes')).toBeInTheDocument()
    expect(screen.getByText('dropped: 2 oversize')).toBeInTheDocument()
    expect(screen.getByRole('status')).not.toHaveTextContent('frames')            // the 2 Hz counts stay outside the live region
    push({ state: 'connecting', frames: 0, bytes: 0 })
    expect(screen.getByRole('status')).toHaveTextContent('Connecting…')
  })

  it('stale shows the banner and the time', () => {
    render(<MonitorPage />)
    push()
    expect(screen.queryByRole('status', { name: /stale/ })).toBeNull()
    const since = new Date(2024, 0, 2, 3, 4, 5).getTime() / 1000
    push({ state: 'stale', stale_since: since })
    expect(screen.getByRole('status', { name: 'Monitor data are stale' })).toHaveTextContent('Monitor data are not updating')
    expect(screen.getByText('Stale since 03:04:05')).toBeInTheDocument()
    expect(document.querySelector('.monitor-grid')).toHaveClass('is-stale')
    push()   // the publisher came back
    expect(document.querySelector('.monitor-grid')).not.toHaveClass('is-stale')
  })

  it('collapsed panels stay collapsed after remount', async () => {
    push()
    const first = render(<MonitorPage />)
    const toggle = () => screen.getByRole('button', { name: /Ports/ })
    expect(toggle()).toHaveAttribute('aria-expanded', 'true')
    await userEvent.click(toggle())
    expect(toggle()).toHaveAttribute('aria-expanded', 'false')
    expect(JSON.parse(localStorage.getItem('trex-web-gui.monitorPanels') ?? '{}')).toEqual({ ports: true })
    first.unmount()
    render(<MonitorPage />)
    expect(toggle()).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByRole('button', { name: /Global/ })).toHaveAttribute('aria-expanded', 'true')
    expect(within(screen.getByRole('region', { name: /Global/ })).getByRole('group', { name: 'Tx' })).toBeVisible()
  })
})
