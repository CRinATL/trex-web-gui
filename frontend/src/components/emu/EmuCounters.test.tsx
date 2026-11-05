import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import type { EmuCounters as Body, EmuMsg } from '../../api'
import { emuStore } from '../../emuStore'
import emuMsg from '../../__fixtures__/emuMsg.json'
import fixture from '../../__fixtures__/emuCounters.json'
import { EmuCounters } from './EmuCounters'

const connected = emuMsg.connected as EmuMsg
const ctx = fixture.ctx as Body
const nsBody = fixture.ns as Body
const clientBody = fixture.client as Body
const ns = { vport: 1, tci: [0, 0], tpid: [0, 0] }
const mac = [0, 0, 0, 1, 0, 0]
const tick = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })
const withRow = (b: Body, over: Partial<api.EmuCounterRow>): Body => ({ ...b, tables: { ...b.tables, ctx: ctx.tables.ctx.map((r) => (r.name === 'activeNs' ? { ...r, ...over } : r)) } })

beforeEach(() => {
  vi.useFakeTimers()
  localStorage.clear()
  act(() => emuStore.push(connected))
})
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

describe('EmuCounters', () => {
  it('reads the global scope every second and shows name, value, unit and a severity text', async () => {
    const get = vi.spyOn(api, 'emuCounters').mockResolvedValue(ctx)
    render(<EmuCounters ns={null} mac={null} />)
    await tick(0)
    expect(get).toHaveBeenCalledWith('ctx', {})
    const r = screen.getByText('activeNs').closest('tr')!
    expect(within(r).getByText('2')).toBeInTheDocument()
    expect(within(r).getByText('ops')).toBeInTheDocument()
    expect(within(r).getByText('INFO')).toBeInTheDocument()
    expect(screen.getByText('activeNs')).toHaveAttribute('title', 'active ns')
    await tick(1000)
    expect(get).toHaveBeenCalledTimes(2)
  })
  it('names WARNING and ERROR in text, not only in colour', async () => {
    const body: Body = { unsupported: [], tables: { t: [
      { name: 'w', value: 1, unit: 'u', help: '', info: 0x13, zero: false },
      { name: 'e', value: 1, unit: 'u', help: '', info: 0x14, zero: false },
    ] } }
    vi.spyOn(api, 'emuCounters').mockResolvedValue(body)
    render(<EmuCounters ns={null} mac={null} />)
    await tick(0)
    expect(within(screen.getByText('w').closest('tr')!).getByText('WARNING')).toBeInTheDocument()
    expect(within(screen.getByText('e').closest('tr')!).getByText('ERROR')).toBeInTheDocument()
  })
  it('hides zero counters by default and shows them when asked; a null value reads as a dash', async () => {
    const body: Body = { unsupported: [], tables: { t: [
      { name: 'zero', value: 0, unit: 'u', help: '', info: 0x12, zero: true },
      { name: 'meta', value: null, unit: 'u', help: '', info: 0x12, zero: false },
      { name: 'some', value: 4, unit: 'u', help: '', info: 0x12, zero: false },
    ] } }
    vi.spyOn(api, 'emuCounters').mockResolvedValue(body)
    render(<EmuCounters ns={null} mac={null} />)
    await tick(0)
    expect(screen.queryByText('zero')).toBeNull()
    expect(within(screen.getByText('meta').closest('tr')!).getByText('—')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Hide zeros' }))
    expect(screen.getByText('zero')).toBeInTheDocument()
  })
  it('Clear shows the change since, labelled, with Show totals to go back, and makes no read', async () => {
    let n = 5
    const get = vi.spyOn(api, 'emuCounters').mockImplementation(async () => withRow(ctx, { value: n }))
    render(<EmuCounters ns={null} mac={null} />)
    await tick(0)
    expect(screen.queryByText(/^since /)).toBeNull()
    const calls = get.mock.calls.length
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }))
    expect(get.mock.calls.length).toBe(calls)
    expect(screen.getByText(/^since \d\d:\d\d:\d\d$/)).toBeInTheDocument()
    expect(screen.queryByText('activeNs')).toBeNull()   // delta 0, hidden with the zeros
    n = 8
    await tick(1000)
    expect(within(screen.getByText('activeNs').closest('tr')!).getByText('3')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Show totals' }))
    expect(within(screen.getByText('activeNs').closest('tr')!).getByText('8')).toBeInTheDocument()
    expect(screen.queryByText(/^since /)).toBeNull()
  })
  it('keeps the baseline in the browser: a remounted panel still shows deltas', async () => {
    vi.spyOn(api, 'emuCounters').mockResolvedValue(withRow(ctx, { value: 5 }))
    const first = render(<EmuCounters ns={null} mac={null} />)
    await tick(0)
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }))
    first.unmount()
    render(<EmuCounters ns={null} mac={null} />)
    await tick(0)
    expect(screen.getByText(/^since /)).toBeInTheDocument()
  })
  it('a counter absent from the baseline shows its total', async () => {
    const get = vi.spyOn(api, 'emuCounters').mockResolvedValue(ctx)
    render(<EmuCounters ns={null} mac={null} />)
    await tick(0)
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }))
    get.mockResolvedValue({ unsupported: [], tables: { ...ctx.tables, fresh: [{ name: 'newOne', value: 7, unit: 'u', help: '', info: 0x12, zero: false }] } })
    await tick(1000)
    expect(within(screen.getByText('newOne').closest('tr')!).getByText('7')).toBeInTheDocument()
  })
  it('shows an unsupported plugin as a muted row, not an error', async () => {
    vi.spyOn(api, 'emuCounters').mockResolvedValue(nsBody)
    render(<EmuCounters ns={ns} mac={null} />)
    fireEvent.click(screen.getByRole('button', { name: 'Namespace' }))
    await tick(0)
    const li = screen.getByText('dns — no counters here (not loaded, or not supported by this server)')
    expect(li).toHaveClass('unsupported-row')
    expect(screen.queryByRole('alert')).toBeNull()
  })
  it('switches scope: Namespace sends the namespace, Client also its MAC; each says what it needs', async () => {
    const get = vi.spyOn(api, 'emuCounters').mockImplementation(async (s) => (s === 'client' ? clientBody : s === 'ns' ? nsBody : ctx))
    const { rerender } = render(<EmuCounters ns={null} mac={null} />)
    get.mockClear()
    fireEvent.click(screen.getByRole('button', { name: 'Namespace' }))
    expect(screen.getByText(/Select a namespace/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Client' }))
    expect(screen.getByText(/Select a client/)).toBeInTheDocument()
    await tick(0)
    expect(get).not.toHaveBeenCalled()
    rerender(<EmuCounters ns={ns} mac={null} />)
    expect(screen.getByText(/Select a client/)).toBeInTheDocument()
    rerender(<EmuCounters ns={ns} mac={mac} />)
    await tick(0)
    expect(get).toHaveBeenLastCalledWith('client', { ns, mac })
    expect(screen.queryByText('pktRxMDnsQuery')).toBeNull()   // all zero, hidden
    fireEvent.click(screen.getByRole('checkbox', { name: 'Hide zeros' }))
    expect(screen.getByText('pktRxMDnsQuery')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Namespace' }))
    await tick(0)
    expect(get).toHaveBeenLastCalledWith('ns', { ns })
    fireEvent.click(screen.getByRole('button', { name: 'Global' }))
    await tick(0)
    expect(get).toHaveBeenLastCalledWith('ctx', {})
  })
  it('says a failed read', async () => {
    vi.spyOn(api, 'emuCounters').mockRejectedValue(new Error('boom'))
    render(<EmuCounters ns={null} mac={null} />)
    await tick(0)
    expect(screen.getByRole('alert')).toHaveTextContent('Could not read the counters: boom')
  })
  it('a counter in the baseline that later reads 0 shows its change, not nothing', async () => {
    let n = 5
    vi.spyOn(api, 'emuCounters').mockImplementation(async () => withRow(ctx, { value: n }))
    render(<EmuCounters ns={null} mac={null} />)
    await tick(0)
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }))
    n = 0
    await tick(1000)
    expect(within(screen.getByText('activeNs').closest('tr')!).getByText('-5')).toBeInTheDocument()
  })
  it('shows a neutral dash, never a code, for a counter the meta does not describe or an unknown code', async () => {
    vi.spyOn(api, 'emuCounters').mockResolvedValue({ unsupported: [], tables: { t: [
      { name: 'nometa', value: 1, unit: '', help: '', info: null, zero: false },
      { name: 'odd', value: 1, unit: '', help: '', info: 99, zero: false },
    ] } })
    render(<EmuCounters ns={null} mac={null} />)
    await tick(0)
    for (const n of ['nometa', 'odd']) expect(within(screen.getByText(n).closest('tr')!).getAllByText('—')).toHaveLength(1)
    expect(screen.queryByText(/code/)).toBeNull()
  })
  it('drops the baselines when the connection is established anew, so a restarted server gives no negative deltas', async () => {
    vi.spyOn(api, 'emuCounters').mockResolvedValue(withRow(ctx, { value: 5 }))
    render(<EmuCounters ns={null} mac={null} />)
    await tick(0)
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }))
    expect(screen.getByText(/^since /)).toBeInTheDocument()
    act(() => emuStore.push({ ...connected, state: 'lost' }))
    act(() => emuStore.push(connected))
    await tick(0)
    expect(screen.queryByText(/^since /)).toBeNull()
    expect(localStorage.getItem('trex-web-gui.emuBaseline')).toBeNull()
  })
})
