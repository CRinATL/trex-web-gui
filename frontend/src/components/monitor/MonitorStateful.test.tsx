import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import type { MonitorMsg } from '../../api'
import { monitorStore } from '../../monitorStore'
import stateful from '../../__fixtures__/monitorStateful.json'
import { MonitorLatencyV2 } from './MonitorLatencyV2'
import { MonitorRxCheck } from './MonitorRxCheck'
import { MonitorTemplates } from './MonitorTemplates'

const base = stateful as unknown as MonitorMsg
beforeEach(() => { monitorStore.reset() })

describe('Monitor stateful panels', () => {
  it('latency v2 shows every port', () => {
    const port = base.latency_v2!['port-0']
    render(<MonitorLatencyV2 />)
    act(() => monitorStore.push({ ...base, latency_v2: { ...base.latency_v2, 'port-2': port, 'port-3': port } }))
    for (const n of [0, 1, 2, 3]) {
      const block = screen.getByRole('region', { name: `Latency v2 port ${n}` })
      expect(within(block).getByRole('heading', { name: `Port ${n}` })).toBeInTheDocument()
      expect(within(block).getAllByTestId('hist-bar').length).toBeGreaterThan(0)
      expect(within(block).getByLabelText(`Port ${n} counters`)).toBeInTheDocument()
    }
    expect(within(screen.getByRole('region', { name: 'Latency v2 port 0' })).getByText('m_pkt_ok').nextElementSibling).toHaveTextContent(/1[.,]?5/)
  })

  it('rx-check log scale narrows the ratio', async () => {
    render(<MonitorRxCheck />)
    const hist = { cnt: 1001, high_cnt: 1001, histogram: [{ key: 100, val: 1 }, { key: 200, val: 10000 }], max_usec: 0, min_usec: 10, s_avg: 1, s_max: 0 }
    act(() => monitorStore.push({ ...base, rx_check: { ...base.rx_check, latency_hist: hist } }))
    const ratio = () => { const w = screen.getAllByTestId('hist-bar').map((g) => Number(g.querySelector('rect')!.getAttribute('width'))); return w[1] / w[0] }
    expect(ratio()).toBeGreaterThan(100)
    await userEvent.selectOptions(screen.getByLabelText('Scale'), 'Log')
    expect(ratio()).toBeLessThan(20)
    expect(screen.getByText('m_total_rx')).toBeInTheDocument()   // the counters stay beside the histogram
  })

  it('templates name the dns cap', () => {
    render(<MonitorTemplates />)
    act(() => monitorStore.push(base))
    const rows = within(screen.getByRole('table', { name: 'Templates' })).getAllByRole('row').slice(1)
    expect(rows.map((r) => within(r).getByRole('rowheader').textContent)).toEqual(['cap2/Oracle.pcap', 'cap2/smtp.pcap', 'cap2/dns.pcap'])
    expect(rows[2]).toHaveTextContent('212')
    expect(screen.queryByText('check')).toBeNull()
  })

  it('a flagged template row says check', () => {
    render(<MonitorTemplates />)
    act(() => monitorStore.push({ ...base, global: { ...base.global, template: [400, 0, 0] }, rx_check: { template: [{ id: 0, val: 0, rx_pkts: 100, jitter: 0 }] } }))
    expect(screen.getAllByText('check')).toHaveLength(1)
    expect(screen.getByText('check').closest('tr')).toHaveTextContent('cap2/Oracle.pcap')
  })
})
