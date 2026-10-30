import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { BusyKind, PingMsg } from '../api'
import { wireLayerPort, wirePing } from '../testWire'
import { PingPanel } from './PingPanel'

let busy: Record<number, BusyKind> = {}
let log: PingMsg[] = []
const runner = vi.fn()
vi.mock('../state', () => ({ useBusy: () => busy, usePingLog: () => log, usePingRunner: () => runner }))
beforeEach(() => { busy = {}; log = []; runner.mockReset(); runner.mockResolvedValue({ run: 1, lines: [], service_restore_error: null }) })

const resolved = () => wireLayerPort('l3_resolved', { owned: true })
const unresolved = () => wireLayerPort('l3_unresolved', { owned: true })
const ping = () => userEvent.click(screen.getByRole('button', { name: 'Ping' }))
const dst = (v: string) => userEvent.type(screen.getByLabelText('Destination'), v)

describe('PingPanel', () => {
  it('lines render in order with the summary', () => {
    log = [wirePing('reply', { run: 4, seq: 1, count: 3 }), wirePing('timeout', { run: 4, seq: 2, count: 3 }),
      wirePing('reply', { run: 4, seq: 3, count: 3, text: 'Reply from 2.2.2.2: third' })]
    render(<PingPanel port={resolved()} why={null} />)
    const items = screen.getAllByRole('listitem').map((x) => x.textContent)
    expect(items).toEqual(['Reply from 2.2.2.2: bytes=64, time=4.97ms, TTL=128', 'Request timed out.', 'Reply from 2.2.2.2: third'])
    expect(screen.getByText('3 sent, 2 received')).toBeInTheDocument()
  })

  it('shows only the newest run and does not count refused and error lines as sent', () => {
    log = [wirePing('reply', { run: 1, seq: 1, count: 5 }),
      wirePing('reply', { run: 2, seq: 1, count: 5 }),
      wirePing('reply', { run: 2, seq: 2, count: 5, status: 'refused', text: 'Port 0 is transmitting; stop traffic first.', rtt_ms: null })]
    render(<PingPanel port={resolved()} why={null} />)
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
    expect(screen.getByText('Port 0 is transmitting; stop traffic first.')).toBeInTheDocument()
    expect(screen.getByText('1 sent, 1 received')).toBeInTheDocument()
  })

  it('has no summary while the port is pinging', () => {
    busy = { 0: 'ping' }
    log = [wirePing('reply', { run: 1, seq: 1, count: 5 })]
    render(<PingPanel port={resolved()} why={null} />)
    expect(screen.queryByText(/sent,/)).toBeNull()
  })

  it('keeps the summary when the port later becomes busy with a scan or Apply', () => {
    busy = { 0: 'scan' }
    log = [wirePing('reply', { run: 1, seq: 1, count: 1 })]
    render(<PingPanel port={resolved()} why="busy: scanning" />)
    expect(screen.getByText('1 sent, 1 received')).toBeInTheDocument()
  })

  it('an IPv4 ping without resolved L3 is refused before sending', async () => {
    render(<PingPanel port={unresolved()} why={null} />)
    await dst('1.1.1.99'); await ping()
    expect(screen.getByRole('alert')).toHaveTextContent('ARP resolution required: configure L3 IPv4 first.')
    expect(runner).not.toHaveBeenCalled()
  })

  it('sends the defaults to a resolved IPv4 destination', async () => {
    render(<PingPanel port={resolved()} why={null} />)
    await dst('2.2.2.2'); await ping()
    expect(runner).toHaveBeenCalledWith(0, '2.2.2.2', 5, 64)
  })

  it('an IPv6 destination needs no resolved L3', async () => {
    render(<PingPanel port={unresolved()} why={null} />)
    await dst('fe80::5052:18ff:fe89:91ea'); await ping()
    expect(runner).toHaveBeenCalledWith(0, 'fe80::5052:18ff:fe89:91ea', 5, 64)
  })

  it('refuses a bad destination, count and size before sending', async () => {
    render(<PingPanel port={resolved()} why={null} />)
    await dst('nonsense')
    const count = screen.getByLabelText('Count'), size = screen.getByLabelText('Size')
    await userEvent.clear(count); await userEvent.type(count, '11')
    await userEvent.clear(size); await userEvent.type(size, '63')
    await ping()
    const text = screen.getAllByRole('alert').map((a) => a.textContent).join(' ')
    expect(text).toMatch(/IPv4 or IPv6/); expect(text).toMatch(/1 to 10/); expect(text).toMatch(/64 to 9216/)
    expect(runner).not.toHaveBeenCalled()
  })

  it('Ping is disabled while busy', () => {
    busy = { 0: 'ping' }
    render(<PingPanel port={resolved()} why="busy: pinging" />)
    expect(screen.getByRole('button', { name: 'Ping' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Ping' })).toHaveAttribute('title', 'busy: pinging')
  })

  it('shows a rejected ping and a failed service restore', async () => {
    runner.mockRejectedValueOnce(new Error('Port 0 is busy pinging or scanning.'))
    render(<PingPanel port={resolved()} why={null} />)
    await dst('2.2.2.2'); await ping()
    expect(await screen.findByText('Port 0 is busy pinging or scanning.')).toBeInTheDocument()
    runner.mockResolvedValueOnce({ run: 2, lines: [], service_restore_error: 'an active capture' })
    await ping()
    expect(await screen.findByText(/Service mode could not be restored: an active capture/)).toBeInTheDocument()
  })

  it('states that Ping and Scan put the port in service mode', () => {
    render(<PingPanel port={resolved()} why={null} />)
    expect(screen.getByText('Ping and Scan put the port in service mode while they run.')).toBeInTheDocument()
  })
})
