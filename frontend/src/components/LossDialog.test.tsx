import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import { LossDialog } from './LossDialog'

afterEach(() => vi.restoreAllMocks())

const params = { host: '127.0.0.1', sync_port: 4501, async_port: 4500, timeout_s: 3, read_only: false }
const STOPPED = 'TRex server stopped: the TRex server process (_t-rex-64) on this host has exited.'
const base = { type: 'session' as const, params, info: null, rpc_console: false, include_polling: false, ifaces: [], ifaces_note: null }
const lost = (seq: string, error: string | null = STOPPED): api.SessionSnapshot => ({ ...base, state: 'lost', error, connect_seq: seq })
const connected = (seq: string): api.SessionSnapshot => ({ ...base, state: 'connected', error: null, connect_seq: seq })

function open(session: api.SessionSnapshot) {
  const page = (s: api.SessionSnapshot) => (
    <MemoryRouter initialEntries={['/control']}>
      <LossDialog session={s} />
      <Routes><Route path="/events" element={<p>events page</p>} /><Route path="*" element={null} /></Routes>
    </MemoryRouter>
  )
  const r = render(page(session))
  return { ...r, again: (s: api.SessionSnapshot) => r.rerender(page(s)) }
}
const dialog = () => screen.queryByRole('dialog', { name: 'Connection to TRex lost' })

describe('LossDialog', () => {
  it('opens on a loss with its cause, Reconnect focused', () => {
    open(lost('p:1'))
    expect(dialog()).toBeInTheDocument()
    expect(dialog()).toHaveTextContent(STOPPED)
    expect(screen.getByRole('button', { name: 'Reconnect' })).toHaveFocus()
  })
  it('is not shown connected, nor after a Disconnect (no loss)', () => {
    const { again } = open(connected('p:1'))
    expect(dialog()).toBeNull()
    again({ ...connected('p:1'), state: 'disconnected' })
    expect(dialog()).toBeNull()
  })
  it('opens once per loss: Close keeps it shut for that loss, a later loss opens it again', async () => {
    const { again } = open(lost('p:1'))
    await userEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(dialog()).toBeNull()
    again(lost('p:1', 'later snapshot of the same loss'))
    expect(dialog()).toBeNull()
    again(connected('p:2'))
    again(lost('p:2'))
    expect(dialog()).toBeInTheDocument()
  })
  it('Escape closes it', async () => {
    open(lost('p:1'))
    await userEvent.keyboard('{Escape}')
    expect(dialog()).toBeNull()
  })
  it('Reconnect connects with the lost session\'s parameters and closes', async () => {
    const connect = vi.spyOn(api, 'connect').mockResolvedValue(undefined as never)
    open(lost('p:1'))
    await userEvent.click(screen.getByRole('button', { name: 'Reconnect' }))
    expect(connect).toHaveBeenCalledWith(params)
    expect(dialog()).toBeNull()
  })
  it('See Events opens the Events page and closes', async () => {
    open(lost('p:1'))
    await userEvent.click(screen.getByRole('link', { name: 'See Events' }))
    expect(screen.getByText('events page')).toBeInTheDocument()
    expect(dialog()).toBeNull()
  })
  it('an unknown cause still says the connection was lost', () => {
    open(lost('p:1', null))
    expect(dialog()).toHaveTextContent('unknown cause')
  })
})
