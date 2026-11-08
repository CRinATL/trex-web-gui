import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import UPlot from 'uplot'
import { DASH_VIEW_KEY, resetDashViewStore } from '../dashView'
import { pgidStore } from '../pgidStats'
import { wirePgid, type WirePgid } from '../testPgid'
import { wirePort } from '../testWire'
import { StreamsTab } from './StreamsTable'

const ports = [wirePort('tx_owned', { id: 0 }), wirePort('idle_unowned', { id: 1, owned: false })]
const labelOf = (p: { id: number }) => (p.id === 0 ? { name: 'uplink-A', description: 'to the DUT' } : null)
const reset = () => { pgidStore.reset(); pgidStore.track('streams', []); pgidStore.track('latency', []) }
beforeEach(() => { localStorage.clear(); resetDashViewStore(); reset() })
afterEach(reset)

// n flow pg_ids (1..n) copied from the recorded pg_id 5; `active` lists activeN ids, so activeN > n reads as truncated.
function synth(n: number, activeN = n): WirePgid {
  const m = wirePgid('tx')
  const tpl = m.flow['5']
  m.flow = {}; m.ver_id = {}; m.latency = {}
  const ids = Array.from({ length: n }, (_, i) => i + 1)
  for (const id of ids) { m.flow[String(id)] = JSON.parse(JSON.stringify(tpl)); m.ver_id[String(id)] = 0 }
  m.active = { flow_stats: Array.from({ length: activeN }, (_, i) => i + 1), latency: [] }
  m.truncated = activeN > n
  return m
}

const renderTab = () => render(<StreamsTab ports={ports} labelOf={labelOf} />)
// Store the optional columns as chosen, as a returning visitor would have them.
const showColumns = (v: { bitRates: boolean; counters: boolean }) => { localStorage.setItem(DASH_VIEW_KEY, JSON.stringify(v)); resetDashViewStore() }
const showAll = () => showColumns({ bitRates: true, counters: true })
const headers = () => within(screen.getByRole('table', { name: 'Streams' })).getAllByRole('row')[1].querySelectorAll('th')
const headerTexts = () => Array.from(headers(), (h) => h.textContent)
const rows = () => within(screen.getByRole('table', { name: 'Streams' })).getAllByRole('row').slice(2)   // after the two header rows

describe('StreamsTab table', () => {
  it('renders the recorded tx sample: pg_id 5 as flow and 7 as latency, with its numbers', () => {
    showAll()
    pgidStore.push(wirePgid('tx'))
    renderTab()
    const r = rows()
    expect(r).toHaveLength(2)
    expect(within(r[0]).getByRole('rowheader')).toHaveTextContent('5')
    expect(within(r[0]).getByText('flow')).toBeInTheDocument()
    expect(within(r[1]).getByRole('rowheader')).toHaveTextContent('7')
    expect(within(r[1]).getByText('latency')).toBeInTheDocument()
    expect(within(r[0]).getAllByText('9.09 kpps')).toHaveLength(2)   // Tx and Rx pps
    expect(within(r[0]).getAllByText('7.71 Mbps')).toHaveLength(2)   // Tx bps L1 and Rx bps L1: the server's own
    expect(within(r[0]).getAllByText('90,909')).toHaveLength(2)       // Tx and Rx pkts
    expect(within(r[0]).getAllByText('7,818,181')).toHaveLength(2)
  })
  it('heads the counter columns with "since the stream was added, or Clear"', () => {
    showAll()
    pgidStore.push(wirePgid('tx'))
    renderTab()
    expect(screen.getByRole('columnheader', { name: /since the stream was added, or Clear/ })).toBeInTheDocument()
  })
  it('computes Rx bps L1 when the server sends no rx_bps_l1', () => {
    showAll()
    const m = wirePgid('tx')
    delete m.flow['5'].rx_bps_l1
    pgidStore.push(m)
    renderTab()
    // 6254545.45 + 9090.91 x 160 = 7709091.05
    expect(within(rows()[0]).getAllByText('7.71 Mbps').length).toBeGreaterThanOrEqual(2)
  })
  it('pages at 100 rows, ascending, with a page selector', async () => {
    pgidStore.push(synth(250))
    renderTab()
    expect(rows()).toHaveLength(100)
    expect(within(rows()[0]).getByRole('rowheader')).toHaveTextContent('1')
    expect(within(rows()[99]).getByRole('rowheader')).toHaveTextContent('100')
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Page' }), '3')
    expect(rows()).toHaveLength(50)
    expect(within(rows()[0]).getByRole('rowheader')).toHaveTextContent('201')
    expect(within(rows()[49]).getByRole('rowheader')).toHaveTextContent('250')
  })
  it('says "Showing the first 1151 of M" when truncated, and lists only the 1151', () => {
    pgidStore.push(synth(1151, 1300))
    renderTab()
    expect(screen.getByText('Showing the first 1151 of 1300 streams')).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Page' }).querySelectorAll('option')).toHaveLength(12)
  })
  it('does not say it when not truncated', () => {
    pgidStore.push(wirePgid('tx'))
    renderTab()
    expect(screen.queryByText(/Showing the first/)).toBeNull()
  })
  it('expands a row to per-port values headed by the port title', async () => {
    pgidStore.push(wirePgid('tx'))
    renderTab()
    expect(screen.queryByRole('rowheader', { name: /Port 0/ })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'PG ID 5 ports' }))
    const p0 = screen.getByRole('rowheader', { name: 'Port 0 — uplink-A, to the DUT' })
    expect(within(p0.closest('tr')!).getByText('9.09 kpps')).toBeInTheDocument()   // port 0 Tx pps
    expect(p0).toHaveAttribute('title', 'to the DUT')
    expect(screen.getByRole('rowheader', { name: 'Port 1' })).toBeInTheDocument()
  })
  it('shows – for None values, never NaN', () => {
    showAll()
    const m = wirePgid('tx')
    m.flow['5'].tx_pps.total = null
    m.flow['5'].rx_bps_l1.total = null
    m.flow['5'].tx_pkts.total = null
    pgidStore.push(m)
    renderTab()
    const r = rows()[0]
    expect(r.textContent).not.toMatch(/NaN|null|undefined/)
    expect(within(r).getAllByText('–').length).toBeGreaterThanOrEqual(4)   // Tx pps, Rx bps L1, Tx pkts, loss
  })
  it('shows the global flow errors under the table, by port title', () => {
    const m = wirePgid('tx')
    m.flow_global.rx_err['1'] = 3
    pgidStore.push(m)
    renderTab()
    const dl = screen.getByLabelText('Global flow errors')
    expect(dl).toHaveTextContent('rx_err')
    expect(dl).toHaveTextContent('Port 0 — uplink-A: 0; Port 1: 3')
    expect(within(dl).getAllByText('Port 0 — uplink-A: 0')[0]).toHaveAttribute('title', 'to the DUT')
  })
  it('Clear zeroes the global flow errors, and a later rise shows only the new errors', () => {
    const m = wirePgid('tx')
    m.flow_global.rx_err['1'] = 3
    pgidStore.push(m)
    renderTab()
    act(() => pgidStore.clear())
    expect(screen.getByLabelText('Global flow errors')).toHaveTextContent('Port 1: 0')
    const m2 = wirePgid('tx')
    m2.flow_global.rx_err['1'] = 5
    act(() => pgidStore.push(m2))
    expect(screen.getByLabelText('Global flow errors')).toHaveTextContent('Port 1: 2')
    const m3 = wirePgid('tx')   // the server zeroed it: baseline drops to 0
    m3.flow_global.rx_err['1'] = 1
    act(() => pgidStore.push(m3))
    expect(pgidStore.relativeFlowGlobal('rx_err', '1')).toBe(1)
  })
  it('renders no table or errors while hidden, and the chart history survives hide and show', () => {
    pgidStore.push(synth(6))
    const { rerender } = render(<StreamsTab ports={ports} labelOf={labelOf} visible />)
    expect(rows()).toHaveLength(6)
    const m1 = synth(6); m1.ts = 1
    act(() => pgidStore.push(m1))   // the first push came before the selector tracked anything
    rerender(<StreamsTab ports={ports} labelOf={labelOf} visible={false} />)
    expect(screen.queryByRole('table', { name: 'Streams' })).toBeNull()
    expect(screen.queryByLabelText('Global flow errors')).toBeNull()
    const m = synth(6); m.ts = 2
    act(() => pgidStore.push(m))
    rerender(<StreamsTab ports={ports} labelOf={labelOf} visible />)
    expect(rows()).toHaveLength(6)
    expect(pgidStore.series(1, 'tx_pps', 60)).toHaveLength(2)   // ts 1 (visible) and 2 (hidden)
  })
  it('lists the first 1151 of the active ids when truncated, not a count of the flow keys', () => {
    const m = synth(1151, 1300)
    delete m.flow['1151']   // a key count would now say 1150
    pgidStore.push(m)
    renderTab()
    expect(screen.getByText('Showing the first 1151 of 1300 streams')).toBeInTheDocument()
  })
  it('points a disabled checkbox at the reason line', async () => {
    pgidStore.push(synth(10))
    renderTab()
    for (const i of [5, 6, 7, 8]) await userEvent.click(screen.getByRole('checkbox', { name: `Chart PG ID ${i}` }))
    const ninth = screen.getByRole('checkbox', { name: 'Chart PG ID 9' })
    expect(ninth).toHaveAttribute('aria-describedby', 'pgid-cap-reason-streams')
    expect(document.getElementById('pgid-cap-reason-streams')).toHaveTextContent(/At most 8/)
  })
  it('after Clear a counter cell reads 0 (rendered)', () => {
    showAll()
    pgidStore.push(wirePgid('tx'))
    renderTab()
    act(() => pgidStore.clear())
    const cells = within(rows()[0]).getAllByRole('cell').map((c) => c.textContent)
    expect(cells).not.toContain('90,909')
    expect(cells.filter((c) => c === '0').length).toBeGreaterThanOrEqual(5)
  })
  it('charts the first 4 and gives the chart a series per pick, labelled with its pg_id', () => {
    pgidStore.push(synth(6))
    renderTab()
    const u = (UPlot as unknown as { instances: { opts: { series: { label?: string; stroke?: string }[] } }[] }).instances.at(-1)!
    expect(u.opts.series.slice(1).map((s) => s.label)).toEqual(['PG 1', 'PG 2', 'PG 3', 'PG 4'])
    expect(new Set(u.opts.series.slice(1).map((s) => s.stroke)).size).toBe(4)
  })
})

describe('StreamsTab columns', () => {
  const DEFAULT_HEADS = ['Chart', 'PG ID', 'Type', 'Tx pps', 'Rx pps', 'Loss', 'Loss/s']
  const BITS = ['Tx bps L1', 'Tx bps L2', 'Rx bps L1', 'Rx bps L2']
  const COUNTS = ['Tx pkts', 'Rx pkts', 'Tx bytes', 'Rx bytes']
  const openColumns = async () => { await userEvent.click(screen.getByRole('button', { name: 'Columns ▾' })) }
  it('shows only the default columns', () => {
    pgidStore.push(wirePgid('tx'))
    renderTab()
    expect(headerTexts()).toEqual(DEFAULT_HEADS)
  })
  it('Columns ▾ shows the bit rates', async () => {
    pgidStore.push(wirePgid('tx'))
    renderTab()
    await openColumns()
    await userEvent.click(screen.getByRole('checkbox', { name: 'Bit rates (L1 and L2)' }))
    expect(headerTexts()).toEqual([...DEFAULT_HEADS, ...BITS])
    expect(screen.getByRole('button', { name: 'Columns ▾' })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('group', { name: 'Columns' })).toBeInTheDocument()
  })
  it('Columns ▾ shows the counters with their note', async () => {
    pgidStore.push(wirePgid('tx'))
    renderTab()
    await openColumns()
    await userEvent.click(screen.getByRole('checkbox', { name: 'Packet and byte counters' }))
    expect(headerTexts()).toEqual([...DEFAULT_HEADS, ...COUNTS])
    expect(screen.getByRole('columnheader', { name: /since the stream was added, or Clear/ })).toBeInTheDocument()
  })
  it('keeps the choice across a remount', async () => {
    pgidStore.push(wirePgid('tx'))
    const { unmount } = renderTab()
    await openColumns()
    await userEvent.click(screen.getByRole('checkbox', { name: 'Packet and byte counters' }))
    unmount()
    resetDashViewStore()
    renderTab()
    expect(headerTexts()).toEqual([...DEFAULT_HEADS, ...COUNTS])
  })
  it('aligns per-port rows in every combination', async () => {
    for (const bitRates of [false, true]) for (const counters of [false, true]) {
      localStorage.clear(); showColumns({ bitRates, counters })
      pgidStore.reset(); pgidStore.track('streams', []); pgidStore.push(wirePgid('tx'))
      const { unmount } = renderTab()
      await userEvent.click(screen.getByRole('button', { name: 'PG ID 5 ports' }))
      const width = (tr: Element) => Array.from(tr.children).reduce((n, c) => n + ((c as HTMLTableCellElement).colSpan || 1), 0)
      const want = width(screen.getByRole('table', { name: 'Streams' }).querySelectorAll('thead tr')[1])
      const portRows = screen.getByRole('table', { name: 'Streams' }).querySelectorAll('tr.port-row')
      expect(portRows.length).toBeGreaterThan(0)
      for (const tr of portRows) expect(width(tr), `bitRates=${bitRates} counters=${counters}`).toBe(want)
      unmount()
    }
  })
  it('right-aligns the numbers', () => {
    showAll()
    pgidStore.push(wirePgid('tx'))
    renderTab()
    const tds = Array.from(rows()[0].children)
    expect(tds).toHaveLength(15)
    const values = tds.slice(3)   // after Chart, PG ID and Type
    for (const td of values) expect(td.tagName === 'TD' && td.classList.contains('num'), td.textContent ?? '').toBe(true)
    expect(tds[0].classList.contains('num')).toBe(false)
    expect(within(rows()[0]).getByRole('rowheader')).not.toHaveClass('num')
    expect(tds[2].classList.contains('num')).toBe(false)
  })
  it('the chart keeps its bit-rate metrics', () => {
    pgidStore.push(wirePgid('tx'))
    renderTab()
    const opts = Array.from(screen.getByRole('combobox', { name: 'Chart metric' }).querySelectorAll('option'), (o) => o.textContent)
    expect(opts).toEqual(expect.arrayContaining(['Tx bps L1', 'Tx bps L2', 'Rx bps L2']))
  })
  it('Full-width table stacks the chart', async () => {
    pgidStore.push(wirePgid('tx'))
    const { container } = renderTab()
    expect(container.querySelector('.overview-grid')).not.toHaveClass('wide')
    const chip = screen.getByRole('button', { name: 'Full-width table' })
    expect(chip).toHaveAttribute('aria-pressed', 'false')
    await userEvent.click(chip)
    expect(chip).toHaveAttribute('aria-pressed', 'true')
    expect(container.querySelector('.overview-grid')).toHaveClass('wide')
  })
  it('Escape closes the Columns panel', async () => {
    pgidStore.push(wirePgid('tx'))
    renderTab()
    await openColumns()
    expect(screen.getByRole('group', { name: 'Columns' })).toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    expect(screen.getByRole('button', { name: 'Columns ▾' })).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('group', { name: 'Columns' })).toBeNull()
  })
  it('a press outside closes the Columns panel, one inside does not', async () => {
    pgidStore.push(wirePgid('tx'))
    renderTab()
    await openColumns()
    await userEvent.click(screen.getByRole('checkbox', { name: 'Bit rates (L1 and L2)' }))
    expect(screen.getByRole('group', { name: 'Columns' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('table', { name: 'Streams' }))
    expect(screen.queryByRole('group', { name: 'Columns' })).toBeNull()
  })
  it('renders no controls row with the empty-state message', () => {
    renderTab()
    expect(screen.queryByRole('button', { name: 'Columns ▾' })).toBeNull()
  })
})

describe('StreamsTab update cost (jsdom)', () => {
  it('re-renders 1151 ids per push; median of 10 stays far under a second', () => {
    pgidStore.push(synth(1151))
    renderTab()
    const t: number[] = []
    for (let i = 0; i < 10; i++) {
      const m = synth(1151)
      m.ts = i + 1
      const a = performance.now()
      act(() => pgidStore.push(m))
      t.push(performance.now() - a)
    }
    t.sort((x, y) => x - y)
    // Measured 2026-10-17 (fix 1): median about 34-45 ms (jsdom, uPlot mocked, 100 rows drawn). The bound only catches a blow-up.
    expect((t[4] + t[5]) / 2).toBeLessThan(1000)
    expect(rows()).toHaveLength(100)
  })
})
