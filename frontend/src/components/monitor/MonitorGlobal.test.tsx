import { act, render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { MonitorMsg } from '../../api'
import { monitorStore } from '../../monitorStore'
import { DROP_RATE_HINT } from '../../monitorLayouts'
import stl from '../../__fixtures__/monitorStl.json'
import { MonitorGlobal } from './MonitorGlobal'

const base = stl as unknown as MonitorMsg
const withGlobal = (over: Record<string, number>): MonitorMsg => ({ ...base, global: { ...base.global, ...over } })

describe('MonitorGlobal', () => {
  it('drop rate above 1 bps is flagged in text', () => {
    render(<MonitorGlobal />)
    act(() => monitorStore.push(withGlobal({ m_rx_drop_bps: 1 })))
    expect(screen.queryByText('drops')).toBeNull()
    act(() => monitorStore.push({ ...withGlobal({ m_rx_drop_bps: 2.5 }), ts: base.ts! + 1 }))
    const tile = screen.getByRole('group', { name: 'Drop rate (Tx − Rx)' })
    expect(tile).toHaveAttribute('title', DROP_RATE_HINT)
    expect(tile).toHaveClass('tile-red')
    expect(within(tile).getByText('drops')).toBeInTheDocument()
    expect(tile).toHaveTextContent('2.5 bps')
  })

  it('CPU is a meter', () => {
    render(<MonitorGlobal />)
    act(() => monitorStore.push(withGlobal({ m_cpu_util: 42.5 })))
    const cpu = screen.getByRole('group', { name: 'CPU' })
    expect(within(cpu).getByRole('meter')).toHaveAttribute('value', '42.5')
    expect(cpu).toHaveTextContent('42.5 %')
  })

  it('groups the tiles Tx, Rx, Flows and System, then the expected table', () => {
    render(<MonitorGlobal />)
    act(() => monitorStore.push(withGlobal({ m_tx_expected_pps: 1000, m_tx_pps: 500 })))
    for (const g of ['Tx', 'Rx', 'Flows', 'System']) expect(screen.getByRole('group', { name: g })).toBeInTheDocument()
    expect(within(screen.getByRole('group', { name: 'Tx' })).getByRole('group', { name: 'Tx pps' })).toHaveTextContent('500 pps')
    const row = within(screen.getByRole('table', { name: 'Expected against measured' })).getByRole('row', { name: /Packet rate/ })
    expect(row).toHaveTextContent('1.00 kpps')
    expect(row).toHaveTextContent('50 %')
    expect(within(screen.getByRole('table', { name: 'Expected against measured' })).getByRole('row', { name: /Bit rate/ })).toHaveTextContent('–')
  })

  it('draws the three charts', () => {
    render(<MonitorGlobal />)
    act(() => monitorStore.push(base))
    for (const t of ['Monitor bits per second', 'Monitor packets per second', 'Monitor CPU']) expect(screen.getByRole('figure', { name: t })).toBeInTheDocument()
  })
})
