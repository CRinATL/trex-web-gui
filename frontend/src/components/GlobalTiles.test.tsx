import { act, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { PortView } from '../api'
import { pgidStore } from '../pgidStats'
import { statsStore } from '../stats'
import { wirePgid } from '../testPgid'
import { statsFromSample } from '../testStats'
import { wirePort } from '../testWire'
import { GlobalTiles } from './GlobalTiles'
import { DROP_RATE_HINT } from '../monitorLayouts'

const reset = () => { statsStore.onSession('connected'); statsStore.onSession('disconnected') }
beforeEach(reset)
afterEach(() => { reset(); pgidStore.reset(); vi.useRealTimers() })

const tile = (name: string) => screen.getByRole('group', { name })

describe('GlobalTiles', () => {
  it('renders the Task 1 load sample', () => {
    const m = statsFromSample(3)
    statsStore.push(m)
    render(<GlobalTiles ports={[]} />)
    expect(tile('Tx L2')).toHaveTextContent(/bps/)
    expect(tile('Tx L2')).not.toHaveTextContent('–')
    expect(tile('PPS')).toHaveTextContent(/pps/)
    expect(tile('Tx L1')).not.toHaveTextContent('–')
    expect(tile('Queue full')).toHaveTextContent(String(m.global.queue_full))
  })
  it('shows dashes before any message', () => {
    render(<GlobalTiles ports={[]} />)
    expect(tile('CPU')).toHaveTextContent('–')
    expect(tile('Queue full')).toHaveTextContent('–')
  })
  it('CPU at 85 shows the text "high"; below it does not', () => {
    statsStore.push(statsFromSample(3, { global: { cpu_util: 84.9 } }))
    render(<GlobalTiles ports={[]} />)
    expect(within(tile('CPU')).queryByText('high')).toBeNull()
    expect(tile('CPU')).not.toHaveClass('tile-red')
    act(() => statsStore.push(statsFromSample(3, { global: { cpu_util: 85 } })))
    expect(within(tile('CPU')).getByText('high')).toBeInTheDocument()
    expect(tile('CPU')).toHaveClass('tile-red')
    expect(within(tile('Rx CPU')).queryByText('high')).toBeNull()
  })
  it('a negative or non-finite CPU value is a bare dash, no percent sign', () => {
    statsStore.push(statsFromSample(3, { global: { cpu_util: -4, rx_cpu_util: null } }))
    render(<GlobalTiles ports={[]} />)
    expect(tile('CPU')).toHaveTextContent(/^CPU–$/)
    expect(tile('Rx CPU')).toHaveTextContent(/^Rx CPU–$/)
  })
  it('Rx CPU at 90 shows "high"', () => {
    statsStore.push(statsFromSample(3, { global: { rx_cpu_util: 90 } }))
    render(<GlobalTiles ports={[]} />)
    expect(within(tile('Rx CPU')).getByText('high')).toBeInTheDocument()
  })
  it('active ports counts TX and PCAP_TX and not PAUSE', () => {
    const mk = (id: number, state: string): PortView => ({ ...wirePort('tx_owned'), id, state })
    render(<GlobalTiles ports={[mk(0, 'TX'), mk(1, 'PAUSE'), mk(2, 'PCAP_TX'), mk(3, 'IDLE')]} />)
    expect(tile('Active ports')).toHaveTextContent('2')
  })
})

describe('drop rate', () => {
  it('names what TRex computes and says when it is a loss figure (RC1-41)', () => {
    render(<GlobalTiles ports={[]} />)
    const t = tile('Drop rate (Tx − Rx)')
    expect(t).toHaveAttribute('title', DROP_RATE_HINT)
    expect(DROP_RATE_HINT).toMatch(/sent minus .* received, over all ports/)
    expect(DROP_RATE_HINT).toMatch(/does not come back to a TRex port counts as dropped/)
  })
})

describe('tile groups', () => {
  it('tiles are grouped Tx, Rx, System', () => {
    render(<GlobalTiles ports={[]} />)
    const want: Record<string, string[]> = {
      Tx: ['Tx L2', 'Tx L1', 'PPS', 'Queue full'],
      Rx: ['Rx L2', 'Drop rate (Tx − Rx)'],
      System: ['CPU', 'Rx CPU', 'Active ports', 'Streams'],
    }
    for (const [group, names] of Object.entries(want)) {
      const g = screen.getByRole('group', { name: group })
      expect(within(g).getByRole('heading', { name: group })).toBeVisible()
      expect(within(g).getAllByRole('group').map((t) => t.getAttribute('aria-label'))).toEqual(names)
    }
  })
})

describe('Streams tile', () => {
  it('is a dash before the first pgid message', () => {
    render(<GlobalTiles ports={[]} />)
    expect(tile('Streams')).toHaveTextContent('–')
  })
  it('counts the distinct pg_ids of both types, and follows a new message', () => {
    const m = wirePgid('tx')
    pgidStore.push(m)
    render(<GlobalTiles ports={[]} />)
    const want = new Set([...m.active.flow_stats, ...m.active.latency]).size
    expect(want).toBeGreaterThan(0)
    expect(tile('Streams')).toHaveTextContent(String(want))
    const stopped = wirePgid('stopped')
    act(() => pgidStore.push(stopped))
    expect(tile('Streams')).toHaveTextContent(String(new Set([...stopped.active.flow_stats, ...stopped.active.latency]).size))
  })
  it('an id in both lists is counted once', () => {
    const m = wirePgid('tx')
    m.active = { flow_stats: [3, 5], latency: [5, 9] }
    pgidStore.push(m)
    render(<GlobalTiles ports={[]} />)
    expect(tile('Streams')).toHaveTextContent('3')
  })
})
