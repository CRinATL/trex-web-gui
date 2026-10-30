import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { XstatsView } from '../api'
import { wireXstats } from '../testWire'
import { xstatsStore } from '../xstatsStore'
import { XstatsTab } from './XstatsTab'

const getXstats = vi.fn()
vi.mock('../api', async (orig) => ({ ...(await orig<typeof import('../api')>()), getXstats: (p: number) => getXstats(p) }))

// The recorded view has every counter at 0; two are made non-zero to give the filters something to show.
const view = (extra: Record<string, number> = {}): XstatsView => {
  const w = wireXstats()
  return { port: w.port, values: { ...w.values, tx_good_packets: 12, rx_good_bytes: 7, ...extra } }
}
let docState: 'visible' | 'hidden' = 'visible'
const setDoc = (s: 'visible' | 'hidden') => { docState = s; act(() => { document.dispatchEvent(new Event('visibilitychange')) }) }
const tick = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: false })
  xstatsStore.clear(); getXstats.mockReset(); getXstats.mockImplementation(async () => view())
  docState = 'visible'
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => docState })
})
afterEach(() => { vi.useRealTimers() })

const rows = () => screen.getAllByRole('row').slice(1).map((r) => r.children[1].textContent)   // column 0 is Pin

describe('XstatsTab', () => {
  it('Pin is the first column', async () => {
    render(<XstatsTab port={0} visible connected />)
    await tick(0)
    expect(screen.getAllByRole('columnheader').map((h) => h.textContent)).toEqual(['Pin', 'Counter', 'Value (since Reset counters)'])
    const row = screen.getByText('tx_good_packets').closest('tr')!
    expect(within(row.children[0] as HTMLElement).getByLabelText('Pin tx_good_packets')).toBeInTheDocument()
    expect(row.children[1].textContent).toBe('tx_good_packets')
  })
  it('stops polling while the session is not connected and resumes on connected', async () => {
    const { rerender } = render(<XstatsTab port={0} visible connected />)
    await tick(0)
    expect(getXstats).toHaveBeenCalledTimes(1)
    rerender(<XstatsTab port={0} visible connected={false} />)
    getXstats.mockClear()
    await tick(3000)
    expect(getXstats).not.toHaveBeenCalled()
    rerender(<XstatsTab port={0} visible connected />)
    await tick(0)
    expect(getXstats).toHaveBeenCalledTimes(1)
  })

  it('requests start and stop with the tab and with page visibility', async () => {
    const { rerender } = render(<XstatsTab port={0} visible={false} />)
    await tick(2000)
    expect(getXstats).not.toHaveBeenCalled()
    rerender(<XstatsTab port={0} visible />)
    await tick(0)
    expect(getXstats).toHaveBeenCalledTimes(1)
    expect(getXstats).toHaveBeenCalledWith(0)
    await tick(500)
    expect(getXstats).toHaveBeenCalledTimes(2)
    setDoc('hidden')
    await tick(3000)
    expect(getXstats).toHaveBeenCalledTimes(2)
    setDoc('visible')
    await tick(0)
    expect(getXstats).toHaveBeenCalledTimes(3)
    rerender(<XstatsTab port={0} visible={false} />)
    await tick(3000)
    expect(getXstats).toHaveBeenCalledTimes(3)
  })

  it('no request overlaps a slow one, and the next waits 500 ms after it settles', async () => {
    let release: (v: XstatsView) => void = () => {}
    getXstats.mockImplementationOnce(() => new Promise<XstatsView>((r) => { release = r }))
    render(<XstatsTab port={0} visible />)
    await tick(5000)
    expect(getXstats).toHaveBeenCalledTimes(1)
    release(view())
    await tick(499)
    expect(getXstats).toHaveBeenCalledTimes(1)
    await tick(1)
    expect(getXstats).toHaveBeenCalledTimes(2)
  })

  it('toggling the tab off and on during a slow request leaves one request outstanding', async () => {
    const releases: ((v: XstatsView) => void)[] = []
    getXstats.mockImplementation(() => new Promise<XstatsView>((r) => { releases.push(r) }))
    const { rerender } = render(<XstatsTab port={0} visible />)
    await tick(0)
    rerender(<XstatsTab port={0} visible={false} />)
    rerender(<XstatsTab port={0} visible />)
    await tick(5000)
    expect(getXstats).toHaveBeenCalledTimes(1)
    releases[0](view())
    await tick(499)
    expect(getXstats).toHaveBeenCalledTimes(1)
    await tick(1)
    expect(getXstats).toHaveBeenCalledTimes(2)
  })

  it('an answer that arrives after the store was cleared is dropped', async () => {
    let release: (v: XstatsView) => void = () => {}
    getXstats.mockImplementationOnce(() => new Promise<XstatsView>((r) => { release = r }))
    render(<XstatsTab port={0} visible />)
    await tick(0)
    act(() => xstatsStore.clear())
    release(view())
    await tick(0)
    expect(xstatsStore.names(0)).toEqual([])
  })

  it('not-empty filter on xstats.json', async () => {
    render(<XstatsTab port={0} visible />)
    await tick(0)
    const nonZero = Object.entries(view().values).filter(([, v]) => v !== 0).map(([k]) => k)
    expect(rows().sort()).toEqual(nonZero.sort())
    fireEvent.click(screen.getByLabelText('Not empty'))
    expect(rows()).toHaveLength(Object.keys(view().values).length)
  })

  it('name filter is case-insensitive', async () => {
    render(<XstatsTab port={0} visible />)
    await tick(0)
    fireEvent.change(screen.getByLabelText('Filter'), { target: { value: 'GOOD_P' } })
    expect(rows()).toEqual(['tx_good_packets'])
  })

  it('a pinned row bypasses both filters and updates', async () => {
    render(<XstatsTab port={0} visible />)
    await tick(0)
    fireEvent.click(screen.getByLabelText('Not empty'))
    fireEvent.click(screen.getByLabelText('Pin rx_errors'))   // value 0: hidden by Not empty unless pinned
    fireEvent.click(screen.getByLabelText('Not empty'))
    fireEvent.change(screen.getByLabelText('Filter'), { target: { value: 'tx_good' } })
    expect(rows()[0]).toBe('rx_errors')
    expect(rows().slice(1)).toEqual(['tx_good_packets'])
    getXstats.mockImplementation(async () => view({ rx_errors: 33 }))
    await tick(500)
    const row = screen.getByText('rx_errors').closest('tr')!
    expect(row.children[2].textContent).toBe('33')
  })

  it('Reset counters zeroes the values without a request', async () => {
    render(<XstatsTab port={0} visible />)
    await tick(0)
    const calls = getXstats.mock.calls.length
    expect(screen.getByText('tx_good_packets').closest('tr')!.children[2].textContent).toBe('12')
    const btn = screen.getByRole('button', { name: 'Reset counters' })
    expect(btn.getAttribute('title')).toMatch(/browser/i)
    fireEvent.click(btn)
    expect(getXstats.mock.calls.length).toBe(calls)
    expect(screen.queryByText('tx_good_packets')).toBeNull()   // zero now, hidden by Not empty
    fireEvent.click(screen.getByLabelText('Not empty'))
    expect(screen.getByText('tx_good_packets').closest('tr')!.children[2].textContent).toBe('0')
  })

  it('a failed request shows the notice and retries', async () => {
    getXstats.mockRejectedValueOnce(new Error('boom'))
    render(<XstatsTab port={0} visible />)
    await tick(0)
    expect(screen.getByText('Counters are not updating')).toBeInTheDocument()
    await tick(500)
    expect(getXstats).toHaveBeenCalledTimes(2)
    expect(screen.queryByText('Counters are not updating')).toBeNull()
  })

  it('states the 2^53 limit', () => {
    render(<XstatsTab port={0} visible={false} />)
    expect(screen.getByText(/2\^53/)).toBeInTheDocument()
  })
})
