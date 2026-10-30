import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { SimReport } from '../../api'
import sim from '../../__fixtures__/studioSim.json'
import { SimulateDialog } from './SimulateDialog'

afterEach(cleanup)
const recorded = sim.udp_for_benchmarks as unknown as SimReport

describe('SimulateDialog', () => {
  it('shows round trip, rates, totals and chains', async () => {
    const onClose = vi.fn()
    render(<SimulateDialog report={recorded} onClose={onClose} />)
    const dlg = screen.getByRole('dialog', { name: 'Simulate' })
    expect(within(dlg).getByText('Round trip: identical')).toBeInTheDocument()
    const rows = within(dlg).getAllByRole('row')
    expect(rows[0]).toHaveTextContent(/#\s*Name\s*Length\s*Mode\s*Rate as given\s*pps\s*bps L1\s*bps L2\s*Packets/)
    expect(within(rows[1]).getAllByRole('cell').map((c) => c.textContent))
      .toEqual(['0', '', '128', 'continuous', '1 pps', '1 pps', '1.18 kbps', '1.02 kbps', '–'])
    expect(within(within(dlg).getAllByRole('row')[2]).getAllByRole('cell').map((c) => c.textContent)).toEqual(['1 pps', '1.18 kbps', '1.02 kbps', '–'])
    expect(within(dlg).getByText('#0 (runs until stopped)')).toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalled()
  })

  it('a differing round trip, a stop chain, a loop, unreachable streams and notes', () => {
    const report: SimReport = {
      ...recorded,
      round_trip: { equal: false, differences: ['stream 0 isg: 0 != 10'] },
      chains: [
        { start: 'S0', steps: ['S0', 'S1', 'S2'], end: 'stop', loop_to: null, action_count: 0, pkts: 30000 },
        { start: '1', steps: ['1', '2'], end: 'loop', loop_to: '1', action_count: 3, pkts: 6 },
        { start: 'a', steps: ['a', 'b'], end: 'loop', loop_to: 'b', action_count: 0, pkts: null },
      ],
      unreachable: ['#4', 'orphan'],
      notes: ['the chain from a loops forever (action_count 0)'],
    }
    render(<SimulateDialog report={report} onClose={vi.fn()} />)
    expect(screen.getByText('Round trip: differs')).toBeInTheDocument()
    expect(screen.getByText('stream 0 isg: 0 != 10')).toBeInTheDocument()
    expect(screen.getByText('S0 → S1 → S2, then stop: 30000 packets')).toBeInTheDocument()
    expect(screen.getByText('1 → 2, loops 3 times: 6 packets')).toBeInTheDocument()
    expect(screen.getByText('a → b, loops to b forever')).toBeInTheDocument()
    expect(screen.getByText('Nothing starts #4, orphan.')).toBeInTheDocument()
    expect(screen.getByText('the chain from a loops forever (action_count 0)')).toBeInTheDocument()
  })

  const pct = (i: number): SimReport['streams'][number] => ({
    ...recorded.streams[0], index: i, rate_type: 'percentage', rate_value: 100, pps: null, bps_l1: null, bps_l2: null, pkts: null, percent: 100,
  })

  it('a page where every stream is a percentage shows a dash in each total cell, not 0', () => {
    const report: SimReport = { ...recorded, streams: [pct(0), pct(1)], totals: { pps: 0, bps_l1: 0, bps_l2: 0, burst_pkts: 0, percent: 200 } }
    render(<SimulateDialog report={report} onClose={vi.fn()} />)
    const total = screen.getAllByRole('row').find((r) => /^Total/.test(r.textContent ?? ''))!
    expect(within(total).getAllByRole('cell').map((c) => c.textContent)).toEqual(['–', '–', '–', '–'])
  })

  it('some streams contributing keeps the sum', () => {
    const report: SimReport = { ...recorded, streams: [recorded.streams[0], pct(1)] }
    render(<SimulateDialog report={report} onClose={vi.fn()} />)
    const total = screen.getAllByRole('row').find((r) => /^Total/.test(r.textContent ?? ''))!
    expect(within(total).getAllByRole('cell').slice(0, 3).map((c) => c.textContent)).toEqual(['1 pps', '1.18 kbps', '1.02 kbps'])
  })

  it('the upper-bound sentence is on the page once when the backend note carries it', () => {
    const note = "totals add every enabled stream, an upper bound: a chain runs one stream at a time, so the server's max_pps can be lower"
    render(<SimulateDialog report={{ ...recorded, notes: [note] }} onClose={vi.fn()} />)
    expect(screen.getAllByText(/upper bound/)).toHaveLength(1)
  })
})
