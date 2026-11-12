import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import type { PortView } from '../api'
import { statsStore } from '../stats'
import { statsFromSample } from '../testStats'
import { trend } from '../trend'
import { wirePort } from '../testWire'
import { PortsTable, SERVICE_HOW, ServiceModeBanner } from './PortsTable'

const reset = () => { statsStore.onSession('connected'); statsStore.onSession('disconnected') }
beforeEach(reset)
afterEach(reset)

const p0: PortView = wirePort('tx_owned', { id: 0 })
const p1: PortView = wirePort('idle_unowned', { id: 1, owned: false })

describe('PortsTable', () => {
  it('has a column per port plus a total, and the counter rows are headed', () => {
    statsStore.push(statsFromSample(3))
    render(<PortsTable ports={[p0, p1]} />)
    for (const n of ['Port 0', 'Port 1', 'Total']) expect(screen.getByRole('columnheader', { name: n })).toBeInTheDocument()
    expect(screen.getByRole('rowheader', { name: 'Tx L1 bps' })).toBeInTheDocument()
    expect(screen.getByRole('rowheader', { name: 'Rx packets' })).toBeInTheDocument()
    expect(screen.getByText(/since connect, a port Reset or Reset displayed counters/)).toBeInTheDocument()
  })
  it('reads the values from the stats message', () => {
    const m = statsFromSample(3, { ports: { '0': { opackets: 1234567, tx_bps_L1: null }, '1': {} }, total: { opackets: 7 } })
    statsStore.push(m)
    render(<PortsTable ports={[p0, p1]} />)
    const row = screen.getByRole('rowheader', { name: 'Tx packets' }).closest('tr')!
    const cells = Array.from(row.querySelectorAll('td')).map((c) => c.textContent)
    expect(cells).toEqual(['1,234,567', '–', '7'])
    const l1 = screen.getByRole('rowheader', { name: 'Tx L1 bps' }).closest('tr')!
    expect(l1.querySelector('td')).toHaveTextContent('–')
  })
  it('the My selector keeps owned ports and the total', async () => {
    statsStore.push(statsFromSample(3))
    render(<PortsTable ports={[p0, p1]} />)
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Show ports' }), 'My')
    expect(screen.getByRole('columnheader', { name: 'Port 0' })).toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: 'Port 1' })).toBeNull()
    expect(screen.getByRole('columnheader', { name: 'Total (all ports)' })).toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: 'Total' })).toBeNull()
  })
  it('the banner shows for a filtered-service port and not otherwise; the state leads, in bold', () => {
    const { rerender } = render(<ServiceModeBanner ports={[p0]} />)
    expect(screen.queryByRole('status', { name: 'Service mode' })).toBeNull()
    rerender(<ServiceModeBanner ports={[p0, wirePort('service_filtered', { id: 1 })]} />)
    const banner = screen.getByRole('status', { name: 'Service mode' })
    expect(banner).toHaveTextContent(`Service mode is on for port 1. ${SERVICE_HOW}`)
    expect(banner).toHaveClass('banner', 'alert')        // the bright form (RC1-21)
    expect(banner.querySelector('strong')).toHaveTextContent('Service mode is on for port 1.')
    rerender(<ServiceModeBanner ports={[wirePort('tx_owned', { id: 0, service: true }), wirePort('service_filtered', { id: 1 })]} />)
    expect(screen.getByRole('status', { name: 'Service mode' })).toHaveTextContent('Service mode is on for ports 0, 1.')
  })
  it('the banner turns service mode off on the ports this session owns, one at a time (RC1-22)', async () => {
    const spy = vi.spyOn(api, 'setService').mockResolvedValue({ ports: [] })
    const mine = [wirePort('tx_owned', { id: 0, service: true }), wirePort('tx_owned', { id: 2, service_filtered: true })]
    render(<ServiceModeBanner ports={mine} canAct />)
    const button = screen.getByRole('button', { name: 'Turn service mode off for ports 0, 2' })
    expect(button).toHaveAttribute('data-run', 'hold')   // amber: it changes a port's mode, it stops nothing
    expect(screen.getByRole('status', { name: 'Service mode' })).not.toHaveTextContent(SERVICE_HOW)
    await userEvent.click(button)
    expect(spy.mock.calls).toEqual([[{ ports: [0], enabled: false }], [{ ports: [2], enabled: false }]])
    spy.mockRestore()
  })
  it('the banner never acquires: an unowned port keeps the way by hand, and a refusal is shown', async () => {
    const spy = vi.spyOn(api, 'setService').mockRejectedValue(new api.ApiError(409, 'refused', 'Port 0 is busy'))
    const mixed = [wirePort('tx_owned', { id: 0, service: true }), wirePort('service_filtered', { id: 1, owned: false })]
    const { rerender } = render(<ServiceModeBanner ports={mixed} canAct />)
    const banner = screen.getByRole('status', { name: 'Service mode' })
    expect(banner).toHaveTextContent('Service mode is on for ports 0, 1. Turn service mode off for port 0 For port 1, acquire the port on the Control page')
    await userEvent.click(screen.getByRole('button', { name: 'Turn service mode off for port 0' }))
    expect(within(banner).getByRole('alert')).toHaveTextContent('Port 0 is busy')
    rerender(<ServiceModeBanner ports={mixed} />)   // read-only or not connected: no button, only the way by hand
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.getByRole('status', { name: 'Service mode' })).toHaveTextContent(SERVICE_HOW)
    spy.mockRestore()
  })
  it('the ports table no longer carries the banner: the page shows it above the tabs', () => {
    render(<PortsTable ports={[wirePort('service_filtered', { id: 1 })]} />)
    expect(screen.queryByRole('status', { name: 'Service mode' })).toBeNull()
  })
  it('updates when a new message arrives', () => {
    statsStore.push(statsFromSample(3, { ports: { '0': { opackets: 5 } } }))
    render(<PortsTable ports={[p0]} />)
    act(() => statsStore.push(statsFromSample(3, { ports: { '0': { opackets: 9 } } })))
    const row = screen.getByRole('rowheader', { name: 'Tx packets' }).closest('tr')!
    expect(row.querySelector('td')).toHaveTextContent('9')
  })
  it('shows counters relative to Clear and leaves rates raw', () => {
    statsStore.push(statsFromSample(3, { ports: { '0': { opackets: 1000, tx_bps_L1: 5000 }, '1': {} }, total: { opackets: 1000 } }))
    render(<PortsTable ports={[p0, p1]} />)
    act(() => statsStore.clear())
    const row = (name: string) => Array.from(screen.getByRole('rowheader', { name }).closest('tr')!.querySelectorAll('td')).map((c) => c.textContent)
    expect(row('Tx packets')).toEqual(['0', '–', '0'])
    expect(row('Tx L1 bps')[0]).toBe('5.00 kbps')
    act(() => statsStore.push(statsFromSample(3, { ports: { '0': { opackets: 1040, tx_bps_L1: 5000 }, '1': {} }, total: { opackets: 1040 } })))
    expect(row('Tx packets')).toEqual(['40', '–', '40'])
  })
  it('the column header shows the name, and its accessible name carries the description', () => {
    const labelOf = (p: PortView) => (p.id === 0 ? { name: 'uplink-A', description: 'to the DUT' } : null)
    render(<PortsTable ports={[p0, p1]} labelOf={labelOf} />)
    const h = screen.getByRole('columnheader', { name: 'Port 0 — uplink-A, to the DUT' })
    expect(h).toHaveTextContent('Port 0 — uplink-A')
    expect(h).toHaveAttribute('title', 'to the DUT')
    expect(screen.getByRole('columnheader', { name: 'Port 1' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Total' })).toBeInTheDocument()
  })
})

describe('PortsTable bytes fold', () => {
  it('bytes rows are folded until Show bytes', async () => {
    statsStore.push(statsFromSample(3))
    render(<PortsTable ports={[p0]} />)
    const btn = screen.getByRole('button', { name: 'Show bytes' })
    expect(btn).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('rowheader', { name: 'Tx bytes' })).toBeNull()
    expect(screen.getByRole('rowheader', { name: 'Tx packets' })).toBeInTheDocument()
    expect(btn.textContent).toBe('▸ Show bytes')
    await userEvent.click(btn)
    // the button shows its state (RC1-17): a disclosure arrow, and the label says what the next press does
    expect(btn).toHaveAttribute('aria-expanded', 'true')
    expect(btn).toHaveAccessibleName('Hide bytes')
    expect(btn.textContent).toBe('▾ Hide bytes')
    expect(screen.getByRole('rowheader', { name: 'Tx bytes' })).toBeInTheDocument()
    expect(screen.getByRole('rowheader', { name: 'Rx bytes' })).toBeInTheDocument()
    await userEvent.click(btn)
    expect(btn).toHaveAccessibleName('Show bytes')
    expect(screen.queryByRole('rowheader', { name: 'Rx bytes' })).toBeNull()
  })
})

describe('PortsTable counter order', () => {
  it('counter rows run packets, bytes, errors once Show bytes is pressed', async () => {
    statsStore.push(statsFromSample(3))
    render(<PortsTable ports={[p0]} />)
    await userEvent.click(screen.getByRole('button', { name: 'Show bytes' }))
    const names = screen.getAllByRole('rowheader').map((h) => h.textContent).filter((t) => /packets|bytes|errors/.test(t ?? ''))
    expect(names).toEqual(['Tx packets', 'Rx packets', 'Tx bytes', 'Rx bytes', 'Tx errors', 'Rx errors'])
  })
  it('Show bytes controls the bytes rows', () => {
    render(<PortsTable ports={[p0]} />)
    const id = screen.getByRole('button', { name: 'Show bytes' }).getAttribute('aria-controls')!
    expect(document.getElementById(id)).toContainElement(document.querySelector('tbody[hidden] th') as HTMLElement)
  })
})

describe('PortsTable trend chevrons', () => {
  const pushTotals = (values: number[], t0 = 1000) => values.forEach((v, i) => statsStore.push(statsFromSample(3, { ts: t0 + i, total: { tx_bps: v } })))
  const txL2 = () => screen.getByRole('row', { name: /^Tx L2 bps/ })
  // +10 % a step from 1 Mbps, above the 10 kbps floor: the last three trends agree at level 3
  const rising = [1e6, 1.1e6, 1.21e6, 1.331e6, 1.4641e6, 1.61051e6, 1.771561e6]

  it('a steady rise shows one bold up chevron whose label gives the change', () => {
    pushTotals(rising)
    render(<PortsTable ports={[p0, p1]} />)
    const mark = within(txL2()).getByRole('img', { name: 'rising, about 10 %' })
    expect(mark).toHaveTextContent(/^▲$/)
    expect(mark).toHaveClass('trend', 'trend-strong')
    expect(mark).toHaveAttribute('title', `Rising, about 10 %: ${'the weighted change over the last 5 samples, shown once it holds for 3; bold above 5 %, faint from 1 %; never under 10 kbps or 10 pps'}`)
  })
  it('a steady fall shows one down chevron', () => {
    pushTotals([...rising].reverse())
    render(<PortsTable ports={[p0, p1]} />)
    expect(within(txL2()).getByRole('img', { name: /^falling/ })).toHaveTextContent(/^▼$/)
  })
  it('a gentle rise (1-5 %) is faint', () => {
    pushTotals([0, 1, 2, 3, 4, 5, 6].map((i) => 1e6 * 1.02 ** i))
    render(<PortsTable ports={[p0, p1]} />)
    expect(within(txL2()).getByRole('img', { name: /^rising/ })).toHaveClass('trend-weak')
  })
  it('until the trend has held for 3 samples there is no chevron, but its slot is kept so the column does not shift', () => {
    pushTotals(rising.slice(0, 6))
    render(<PortsTable ports={[p0, p1]} />)
    expect(within(txL2()).queryByRole('img')).toBeNull()
    const slot = txL2().querySelector('.trend')
    expect(slot).not.toBeNull()
    expect(slot).toHaveAttribute('aria-hidden', 'true')
    expect(slot).toBeEmptyDOMElement()
  })
  it('a turn shows nothing until the new direction has held for 3 samples', () => {
    pushTotals([1e6, 1.1e6, 1.21e6, 1.331e6, 1.4641e6, 1e6, 1e6])
    render(<PortsTable ports={[p0, p1]} />)
    expect(within(txL2()).queryByRole('img')).toBeNull()
  })
  it('a rate under 10 kbps shows none however it moves, and a pps rate under 10 pps none', () => {
    pushTotals(rising.map((v) => v / 200))   // 5 kbps to 8.9 kbps
    render(<PortsTable ports={[p0, p1]} />)
    expect(within(txL2()).queryByRole('img')).toBeNull()
  })
  it('a pps rate is gated by 10 pps, not 10 kbps', () => {
    [0, 1, 2, 3, 4, 5, 6].forEach((i) => statsStore.push(statsFromSample(3, { ts: 1000 + i, total: { tx_pps: 100 * 1.1 ** i } })))
    render(<PortsTable ports={[p0, p1]} />)
    expect(within(screen.getByRole('row', { name: /^Tx pps/ })).getByRole('img', { name: /^rising/ })).toBeInTheDocument()
  })
  it('the chevron appears as the seventh sample arrives, and goes once the rate is flat', () => {
    pushTotals(rising.slice(0, 6))
    render(<PortsTable ports={[p0, p1]} />)
    act(() => statsStore.push(statsFromSample(3, { ts: 1006, total: { tx_bps: rising[6] } })))
    expect(within(txL2()).getByRole('img', { name: /^rising/ })).toBeInTheDocument()
    for (let i = 0; i < 5; i++) act(() => statsStore.push(statsFromSample(3, { ts: 1007 + i, total: { tx_bps: 5e6 } })))
    const seen = statsStore.series('total', 8).map((p) => p.tx_bps as number)
    expect(trend(seen)).toBe(0)
    expect(within(txL2()).queryByRole('img')).toBeNull()
  })
  it('counter rows carry no chevron or slot', () => {
    pushTotals(rising)
    render(<PortsTable ports={[p0, p1]} />)
    expect(screen.getByRole('row', { name: /^Tx packets/ }).querySelector('.trend')).toBeNull()
  })
})
