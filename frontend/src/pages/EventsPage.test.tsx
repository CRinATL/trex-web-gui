import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as state from '../state'
import type { LogEntry } from '../state'
import { EventsPage } from './EventsPage'

const iso = (ts: number) => {
  const d = new Date(ts * 1000), p = (n: number) => String(n).padStart(2, '0'), m = -d.getTimezoneOffset()
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
    + `${m < 0 ? '-' : '+'}${p(Math.floor(Math.abs(m) / 60))}:${p(Math.abs(m) % 60)}`
}
const entries: LogEntry[] = [
  { kind: 'ERROR', ts: 1, message: 'Start: Boom' },
  { kind: 'WARNING', ts: 2, message: 'Port 1 error' },
  { kind: 'SERVER_EVENT', ts: 3, message: 'Port 0 acquired' },
  { kind: 'INFO', ts: 4, message: 'Capture saved' },
  { kind: 'PING', ts: 5, message: 'Port 0: Reply' },
  { kind: 'MARK', ts: 6, message: 'Disconnected' },
]
const seen = vi.fn()
const clear = vi.fn()

// Puts a fixed app state and fresh log actions behind the page, the way ControlPage.test.tsx does.
function setup(log: LogEntry[] = entries, session: Partial<state.AppState['session']> = {}) {
  vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, log, session: { ...state.initialState.session, ...session } })
  vi.spyOn(state, 'useLogActions').mockReturnValue({ seen, clear })
}
const lines = (list: LogEntry[]) => list.map((e) => (e.kind === 'MARK' ? `--- ${iso(e.ts)} ${e.message} ---` : `[${e.kind}] ${iso(e.ts)} ${e.message}`)).join('\n')

beforeEach(() => { seen.mockClear(); clear.mockClear() })
const realCreate = URL.createObjectURL, realRevoke = URL.revokeObjectURL   // jsdom has neither; a test that stubs them puts them back
// Puts a URL function back; where jsdom had none the property is deleted, not set to undefined, so a late call fails as a missing function would.
const restoreUrl = (name: 'createObjectURL' | 'revokeObjectURL', original: unknown) => { if (original === undefined) Reflect.deleteProperty(URL, name); else Reflect.set(URL, name, original) }
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); restoreUrl('createObjectURL', realCreate); restoreUrl('revokeObjectURL', realRevoke) })

describe('EventsPage', () => {
  it('kind chips filter and show counts', async () => {
    setup()
    render(<EventsPage />)
    expect(screen.getByRole('button', { name: 'All 5' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Errors 1' })).toHaveAttribute('aria-pressed', 'false')
    await userEvent.click(screen.getByRole('button', { name: 'Errors 1' }))
    expect(screen.getByRole('button', { name: 'All 5' })).toHaveAttribute('aria-pressed', 'false')
    const table = screen.getByRole('table', { name: 'Event log' })
    expect(within(table).getByText('Start: Boom')).toBeInTheDocument()
    expect(within(table).queryByText('Port 1 error')).toBeNull()
    expect(within(table).getByText(/Disconnected/)).toBeInTheDocument()   // a mark is never filtered out
    await userEvent.click(screen.getByRole('button', { name: 'Warnings 1' }))
    expect(within(table).getByText('Port 1 error')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'All 5' }))
    expect(screen.getByRole('button', { name: 'Errors 1' })).toHaveAttribute('aria-pressed', 'false')
    expect(within(table).getByText('Capture saved')).toBeInTheDocument()
  })
  it('the text filter is case-insensitive', async () => {
    setup()
    render(<EventsPage />)
    await userEvent.type(screen.getByRole('textbox', { name: 'Filter events' }), 'bOOM')
    const table = screen.getByRole('table', { name: 'Event log' })
    expect(within(table).getByText('Start: Boom')).toBeInTheDocument()
    expect(within(table).queryByText('Capture saved')).toBeNull()
  })
  it('Copy takes only the filtered entries', async () => {
    setup()
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })
    render(<EventsPage />)
    await userEvent.type(screen.getByRole('textbox', { name: 'Filter events' }), 'port 0')
    await userEvent.click(screen.getByRole('button', { name: 'Copy' }))
    expect(writeText).toHaveBeenCalledWith(lines([entries[2], entries[4], entries[5]]))
  })
  it('Copy failing shows an alert', async () => {
    setup()
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } })
    render(<EventsPage />)
    await userEvent.click(screen.getByRole('button', { name: 'Copy' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('denied')
  })
  it('the empty state disables Copy and Save log…', () => {
    setup([])
    render(<EventsPage />)
    expect(screen.getByText('No log entries yet.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Copy' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Save log…' })).toBeDisabled()
  })
  it('Save log downloads a txt of the filtered entries', async () => {
    setup()
    const create = vi.fn().mockReturnValue('blob:x')
    URL.createObjectURL = create; URL.revokeObjectURL = vi.fn()
    const names: string[] = []
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { names.push(this.download) })
    render(<EventsPage />)
    await userEvent.type(screen.getByRole('textbox', { name: 'Filter events' }), 'capture')
    await userEvent.click(screen.getByRole('button', { name: 'Save log…' }))
    expect(names).toHaveLength(1)
    expect(names[0]).toMatch(/^trex-web-gui-events-\d{8}-\d{6}Z\.txt$/)
    const blob = create.mock.calls[0][0] as Blob
    const text = await new Promise<string>((res) => { const r = new FileReader(); r.onload = () => res(String(r.result)); r.readAsText(blob) })
    expect(text).toBe(lines([entries[3], entries[5]]) + '\n')
  })
  it('opening the page marks entries seen', () => {
    setup()
    const { rerender } = render(<EventsPage />)
    expect(seen).toHaveBeenCalledTimes(1)
    setup([...entries, { kind: 'ERROR', ts: 7, message: 'later' }])
    rerender(<EventsPage />)
    expect(seen).toHaveBeenCalledTimes(2)
  })
  it('Clear is a danger button that empties the log', async () => {
    setup()
    render(<EventsPage />)
    const btn = screen.getByRole('button', { name: 'Clear' })
    expect(btn).toHaveClass('danger')
    await userEvent.click(btn)
    expect(clear).toHaveBeenCalledTimes(1)
  })
  it('the RPC tab holds the console', async () => {
    setup(entries, { rpc_console: true })
    render(<EventsPage />)
    expect(screen.getByRole('tab', { name: 'Events' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByLabelText('Capture')).not.toBeVisible()
    await userEvent.click(screen.getByRole('tab', { name: 'RPC' }))
    expect(screen.getByRole('checkbox', { name: 'Capture' })).toBeVisible()
  })
  it('Pop out opens the pop-out window, and is hidden when BroadcastChannel is missing', async () => {
    setup()
    const open = vi.fn()
    vi.stubGlobal('open', open)
    vi.stubGlobal('BroadcastChannel', class { postMessage() { /* none */ } close() { /* none */ } })
    const { unmount } = render(<EventsPage />)
    await userEvent.click(screen.getByRole('button', { name: 'Pop out ↗' }))
    expect(open).toHaveBeenCalledWith('/events/popout', 'trex-web-gui-events', 'popup')
    unmount()
    vi.stubGlobal('BroadcastChannel', undefined)
    render(<EventsPage />)
    expect(screen.queryByRole('button', { name: 'Pop out ↗' })).toBeNull()
  })
})

// A recording BroadcastChannel; instances of one name hear each other's posts.
function fakeChannelClass() {
  return class Fake {
    static all: Fake[] = []
    onmessage: ((e: { data: unknown }) => void) | null = null
    posted: { type: string; entries?: unknown }[] = []
    closed = false
    name: string
    constructor(name: string) { this.name = name; Fake.all.push(this) }
    postMessage(data: { type: string; entries?: unknown }) {
      this.posted.push(data)
      for (const c of Fake.all) if (c !== this && !c.closed) c.onmessage?.({ data })
    }
    close() { this.closed = true }
  }
}

describe('EventsPage pop-out', () => {
  it('the pop-out waits for the main window, then shows its log', async () => {
    const Fake = fakeChannelClass()
    vi.stubGlobal('BroadcastChannel', Fake)
    const { act } = await import('@testing-library/react')
    render(<EventsPage popout />)
    expect(screen.getByText('Waiting for the main window')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Pop out ↗' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Clear' })).toBeNull()
    expect(screen.queryByRole('tab', { name: 'RPC' })).toBeNull()
    const main = new Fake('trex-web-gui-events')
    act(() => main.postMessage({ type: 'log', entries: [{ kind: 'INFO', ts: 1, message: 'from main' }] }))
    expect(screen.queryByText('Waiting for the main window')).toBeNull()
    expect(screen.getByText('from main')).toBeInTheDocument()
    act(() => main.postMessage({ type: 'log', entries: [{ kind: 'INFO', ts: 1, message: 'from main' }, { kind: 'INFO', ts: 2, message: 'newer' }] }))
    expect(screen.getByText('newer')).toBeInTheDocument()
  })
})
