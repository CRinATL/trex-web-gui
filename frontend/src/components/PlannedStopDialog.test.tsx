import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import { PlannedStopDialog } from './PlannedStopDialog'

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers() })

const params = { host: '127.0.0.1', sync_port: 4501, async_port: 4500, timeout_s: 3, read_only: false }
const base = { type: 'session' as const, params, info: null, rpc_console: false, include_polling: false, ifaces: [], ifaces_note: null, error: null }
const AT = 1_700_000_000
const stopped = (recovery: api.Recovery, at = AT, seq = 'p:1'): api.SessionSnapshot =>
  ({ ...base, state: 'disconnected', connect_seq: seq, ended: { by: 'daemon_stop', at, daemon: '127.0.0.1:8090', recovery } })
const page = (s: api.SessionSnapshot) => <MemoryRouter><PlannedStopDialog session={s} /></MemoryRouter>
const dialog = () => screen.queryByRole('dialog', { name: 'TRex stopped' })

describe('PlannedStopDialog', () => {
  it('renders from the session message alone', () => {
    render(page(stopped({ kind: 'none' })))
    expect(dialog()).toBeInTheDocument()
    expect(dialog()).toHaveTextContent(`You stopped TRex on 127.0.0.1:8090 from the TRex daemon dialog at ${new Date(AT * 1000).toLocaleTimeString()}.`)
    expect(dialog()).toHaveTextContent('Control is disconnected.')
    expect(dialog()).toHaveTextContent('Nothing restarts it. Start TRex from the TRex daemon dialog to reconnect.')
  })
  it('counts down while reconnecting', () => {
    vi.useFakeTimers()
    vi.setSystemTime(1_700_000_000_000)
    render(page(stopped({ kind: 'reconnecting', deadline: 1_700_000_030, left_s: 30 })))
    expect(dialog()).toHaveTextContent('TRex is starting. Control reconnects by itself, within 30 s.')
    act(() => { vi.advanceTimersByTime(5000) })
    expect(dialog()).toHaveTextContent('within 25 s')
  })
  it('counts down from left_s whatever the browser clock says', () => {
    vi.useFakeTimers()
    vi.setSystemTime((1_700_000_030 + 3600) * 1000)   // an hour after the deadline
    render(page(stopped({ kind: 'reconnecting', deadline: 1_700_000_030, left_s: 30 })))
    expect(dialog()).toHaveTextContent('within 30 s')
    act(() => { vi.advanceTimersByTime(5000) })
    expect(dialog()).toHaveTextContent('within 25 s')
    act(() => { vi.advanceTimersByTime(60000) })
    expect(dialog()).toHaveTextContent('within 0 s')
  })
  it('a new left_s for the same deadline restarts the count from it', () => {
    vi.useFakeTimers()
    vi.setSystemTime(1_700_000_000_000)
    const r = render(page(stopped({ kind: 'reconnecting', deadline: 1_700_000_030, left_s: 30 })))
    act(() => { vi.advanceTimersByTime(10000) })
    expect(dialog()).toHaveTextContent('within 20 s')
    r.rerender(page(stopped({ kind: 'reconnecting', deadline: 1_700_000_030, left_s: 15 })))
    expect(dialog()).toHaveTextContent('within 15 s')
    act(() => { vi.advanceTimersByTime(1000) })
    expect(dialog()).toHaveTextContent('within 14 s')
  })
  it('stf_run names the end and links the Monitor', () => {
    render(page(stopped({ kind: 'stf_run', ends_at: AT + 60 })))
    expect(dialog()).toHaveTextContent('A stateful run has no command port.')
    expect(dialog()).toHaveTextContent(`It ends at ${new Date((AT + 60) * 1000).toLocaleTimeString()}; then nothing runs until TRex is started again.`)
    expect(screen.getByRole('link', { name: 'Open Monitor' })).toHaveAttribute('href', '/monitor')
  })
  it('reconnect_failed shows the error and Reconnect', async () => {
    const spy = vi.spyOn(api, 'connect').mockResolvedValue(undefined as never)
    render(page(stopped({ kind: 'reconnect_failed', error: 'no answer from 127.0.0.1:4501' })))
    expect(dialog()).toHaveTextContent('no answer from 127.0.0.1:4501')
    await userEvent.click(screen.getByRole('button', { name: 'Reconnect' }))
    expect(spy).toHaveBeenCalledWith(params)
    expect(dialog()).toBeNull()
  })
  it('Close keeps it shut for that stop; a new stop opens it', async () => {
    const r = render(page(stopped({ kind: 'none' })))
    await userEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(dialog()).toBeNull()
    r.rerender(page(stopped({ kind: 'none' }, AT, 'p:2')))   // a connect_seq change alone
    expect(dialog()).toBeNull()
    r.rerender(page(stopped({ kind: 'none' }, AT + 100)))
    expect(dialog()).toBeInTheDocument()
  })
  it('closes itself on reconnect', () => {
    const r = render(page(stopped({ kind: 'none' })))
    r.rerender(page({ ...base, state: 'connected', connect_seq: 'p:2', ended: null }))
    expect(dialog()).toBeNull()
  })
  it('a recovery change while open restarts the countdown from now', () => {
    vi.useFakeTimers()
    vi.setSystemTime(1_700_000_000_000)
    const r = render(page(stopped({ kind: 'none' })))
    act(() => { vi.advanceTimersByTime(7000) })
    r.rerender(page(stopped({ kind: 'reconnecting', deadline: 1_700_000_037, left_s: 30 })))
    expect(dialog()).toHaveTextContent('within 30 s')
  })
  it('describes itself by the cause line', () => {
    render(page(stopped({ kind: 'none' })))
    expect(dialog()).toHaveAccessibleDescription(/^You stopped TRex on 127\.0\.0\.1:8090/)
  })
  it('focus: Reconnect when offered, else Close', () => {
    const r = render(page(stopped({ kind: 'reconnect_failed', error: 'e' })))
    expect(screen.getByRole('button', { name: 'Reconnect' })).toHaveFocus()
    r.unmount()
    render(page(stopped({ kind: 'none' })))
    expect(screen.getByRole('button', { name: 'Close' })).toHaveFocus()
  })
  it('has the information theme', () => {
    render(page(stopped({ kind: 'none' })))
    expect(dialog()).toHaveClass('planned-stop')
  })
})
