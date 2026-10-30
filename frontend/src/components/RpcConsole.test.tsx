import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import type { RpcMsg } from '../api'
import { RpcConsole } from './RpcConsole'

const msg = (direction: RpcMsg['direction'], body: unknown): RpcMsg => ({ type: 'rpc', direction, body, ts: 1, thread: 't' })
const polls = [msg('request', { id: 1, method: 'get_async_events' }), msg('response', { id: 1, result: [] })]
const entries = [
  msg('request', { id: 2, method: 'acquire' }),
  msg('response', { id: 2, result: 'ok-token' }),
]

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('RpcConsole', () => {
  it('toggling Capture calls setConsole(true)', async () => {
    const set = vi.spyOn(api, 'setConsole').mockResolvedValue(undefined as never)
    render(<RpcConsole entries={[]} on={false} />)
    await userEvent.click(screen.getByLabelText('Capture'))
    expect(set).toHaveBeenCalledWith(true)
  })
  it('reflects the session flag after a reload with capture on', () => {
    render(<RpcConsole entries={[]} on />)
    expect(screen.getByLabelText('Capture')).toBeChecked()
    expect(screen.getByText('Waiting for RPC traffic…')).toBeInTheDocument()
    expect(screen.queryByText('Capture is off.')).not.toBeInTheDocument()
  })
  it('has no client-side poll filter any more; the server decides', () => {
    render(<RpcConsole entries={[...polls, ...entries]} on />)
    expect(screen.queryByLabelText('Hide event polls')).not.toBeInTheDocument()
    expect(document.querySelector('pre')!.textContent).toContain('get_async_events')
  })
  it('Include polling reflects the session flag and sends it alone', async () => {
    const set = vi.spyOn(api, 'setConsole').mockResolvedValue(undefined as never)
    render(<RpcConsole entries={[]} on includePolling />)
    const box = screen.getByLabelText('Include polling')
    expect(box).toBeChecked()
    expect(box.closest('label')).toHaveAttribute('title', expect.stringContaining('Reset'))
    await userEvent.click(box)
    expect(set).toHaveBeenCalledWith(true, false)
  })
  it('filters by text', async () => {
    render(<RpcConsole entries={entries} on />)
    await userEvent.type(screen.getByLabelText('Filter'), 'ok-token')
    const t = document.querySelector('pre')!.textContent!
    expect(t).toContain('ok-token')
    expect(t).not.toContain('"method":"acquire"')
  })
  it('shows an alert when the capture toggle fails', async () => {
    vi.spyOn(api, 'setConsole').mockRejectedValue(new api.ApiError(409, 'not_connected', 'No session'))
    render(<RpcConsole entries={[]} on={false} />)
    await userEvent.click(screen.getByLabelText('Capture'))
    expect(await screen.findByRole('alert')).toHaveTextContent('No session')
  })
  it('Copy copies the shown lines; a failing copy raises an alert', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })
    render(<RpcConsole entries={entries} on />)
    await userEvent.click(screen.getByRole('button', { name: 'Copy' }))
    expect(writeText).toHaveBeenCalledWith('→ {"id":2,"method":"acquire"}\n← {"id":2,"result":"ok-token"}')
    writeText.mockRejectedValue(new Error('denied'))
    await userEvent.click(screen.getByRole('button', { name: 'Copy' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('denied')
  })
  it('truncates long bodies on screen but Copy copies them whole', async () => {
    const big = 'x'.repeat(9000)
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })
    render(<RpcConsole entries={[msg('request', { id: 5, method: 'm', big })]} on />)
    expect(document.querySelector('pre')!.textContent).toContain('… (truncated; Copy copies the full body)')
    expect(document.querySelector('pre')!.textContent).not.toContain('x'.repeat(9000))
    await userEvent.click(screen.getByRole('button', { name: 'Copy' }))
    expect(writeText.mock.calls[0][0]).toContain(big)
  })
})
