import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StrictMode } from 'react'
import { beforeEach, describe, expect, it } from 'vitest'
import type { MonitorMsg } from '../../api'
import { monitorStore } from '../../monitorStore'
import stateful from '../../__fixtures__/monitorStateful.json'
import { MonitorInspector } from './MonitorInspector'
import { KEY_HINTS } from './inspectorHints'

const base = stateful as unknown as MonitorMsg
const withOther = (other: MonitorMsg['other']): MonitorMsg => ({ ...base, other })
beforeEach(() => { monitorStore.reset() })

describe('MonitorInspector', () => {
  it('a tiny fraction reads ≈ 0 with the exact value in its tooltip, and ts says what it is', () => {
    render(<MonitorInspector />)
    act(() => monitorStore.push(withOther({ extra: { ts: base.ts!, data: { m_tx_pps: 5.731e-8, m_rx_pps: 0.1305, ts: { value: 8, freq: 2 } } } })))
    act(() => screen.getByRole('tab', { name: /^extra/ }).click())
    const leaf = screen.getByText('m_tx_pps').closest('.insp-leaf')!
    expect(leaf).toHaveTextContent('m_tx_pps: ≈ 0')
    expect(leaf).toHaveAttribute('title', '5.731e-8')
    expect(screen.getByText('m_rx_pps').closest('.insp-leaf')).toHaveTextContent('m_rx_pps: 0.1305')
    expect(screen.getByText('ts').closest('summary')).toHaveAttribute('title', KEY_HINTS.ts)
  })
  it('lists known and other names', () => {
    render(<MonitorInspector />)
    act(() => monitorStore.push(withOther({ 'tx-gen': { ts: base.ts! - 3, data: { a: 1 } }, extra: { ts: base.ts!, data: [1] } })))
    const names = within(screen.getByRole('tablist', { name: 'Published names' })).getAllByRole('tab').map((t) => t.textContent)
    expect(names).toEqual([
      'trex-global 0.0 s ago', 'trex-latecny 0.0 s ago', 'trex-latecny-v2 0.0 s ago', 'rx-check 0.0 s ago', 'template_info 0.0 s ago',
      'tx-gen 3.0 s ago', 'extra 0.0 s ago',
    ])
    expect(screen.getByRole('tab', { name: /trex-global/ })).toHaveAttribute('id', 'mon-insp-tab-0')
  })

  it('filter narrows keys', async () => {
    render(<MonitorInspector />)
    act(() => monitorStore.push(withOther({ big: { ts: base.ts!, data: { alpha: { beta: 1, gamma: 2 }, delta: 3 } } })))
    await userEvent.click(screen.getByRole('tab', { name: /big/ }))
    expect(screen.getByText('delta')).toBeInTheDocument()
    await userEvent.type(screen.getByRole('searchbox', { name: 'Filter' }), 'gam')
    expect(screen.getByText('gamma')).toBeInTheDocument()
    expect(screen.getByText('alpha')).toBeInTheDocument()   // the path to a match stays
    expect(screen.queryByText('delta')).toBeNull()
    expect(screen.queryByText('beta')).toBeNull()
  })

  it('truncated entry says so', async () => {
    render(<MonitorInspector />)
    act(() => monitorStore.push(withOther({ huge: { ts: base.ts!, data_text: '{"a": [1, 2', truncated: true, bytes: 70000 } })))
    await userEvent.click(screen.getByRole('tab', { name: /huge/ }))
    expect(screen.getByText('truncated: 70000 bytes in all')).toBeInTheDocument()
    expect(screen.getByText('{"a": [1, 2')).toBeInTheDocument()
  })

  it('node cap says how many are hidden', async () => {
    render(<MonitorInspector />)
    const data = Object.fromEntries(Array.from({ length: 2500 }, (_, i) => [`k${i}`, i]))
    act(() => monitorStore.push(withOther({ wide: { ts: base.ts!, data } })))
    await userEvent.click(screen.getByRole('tab', { name: /wide/ }))
    expect(screen.getByText('500 more not shown')).toBeInTheDocument()
    expect(screen.getByText('k1999')).toBeInTheDocument()
    expect(screen.queryByText('k2000')).toBeNull()
  })

  it('folds below depth 2', async () => {
    render(<MonitorInspector />)
    act(() => monitorStore.push(withOther({ deep: { ts: base.ts!, data: { a: { b: { c: { d: 1 } } } } } })))
    await userEvent.click(screen.getByRole('tab', { name: /deep/ }))
    const open = (k: string) => screen.getByText(k).closest('details')!.open
    expect([open('a'), open('b'), open('c')]).toEqual([true, true, false])
  })

  it('node cap agrees with the total under StrictMode', async () => {
    render(<StrictMode><MonitorInspector /></StrictMode>)
    const data = Object.fromEntries(Array.from({ length: 2500 }, (_, i) => [`k${i}`, i]))
    act(() => monitorStore.push(withOther({ wide: { ts: base.ts!, data } })))
    await userEvent.click(screen.getByRole('tab', { name: /wide/ }))
    const shown = document.querySelectorAll('.insp-leaf').length
    expect(shown).toBe(2000)
    expect(screen.getByText('500 more not shown')).toBeInTheDocument()
  })

  it('filtering an array keeps the element indices', async () => {
    render(<MonitorInspector />)
    act(() => monitorStore.push(withOther({ list: { ts: base.ts!, data: { items: [{ a: 1 }, { a: 2 }, { b: 3 }, { b: 4 }, { b: 5 }, { a: 6 }] } } })))
    await userEvent.click(screen.getByRole('tab', { name: /list/ }))
    await userEvent.type(screen.getByRole('searchbox', { name: 'Filter' }), 'a')
    const keys = [...document.querySelectorAll('.insp-key')].map((e) => e.textContent)
    expect(keys).toContain('5')
    expect(keys).not.toContain('2')
  })

  it('the tabs sit beside the panel', () => {
    const { container } = render(<MonitorInspector />)
    act(() => monitorStore.push(base))
    expect(container.querySelector('.monitor-inspector')).toHaveClass('side-layout')
  })
})
