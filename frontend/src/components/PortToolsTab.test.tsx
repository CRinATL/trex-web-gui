import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { BusyKind } from '../api'
import { wireLayerPort } from '../testWire'
import { PortToolsTab } from './PortToolsTab'

let busy: Record<number, BusyKind> = {}
vi.mock('../state', () => ({
  useBusy: () => busy, usePingLog: () => [], usePingRunner: () => vi.fn(),
  useNeighbours: () => undefined, useNeighbourActions: () => ({ set: vi.fn(), clear: vi.fn() }),
}))
beforeEach(() => { busy = {} })
afterEach(() => vi.restoreAllMocks())

const owned = () => wireLayerPort('l3_resolved', { owned: true })
const sel = { kind: 'port', id: 0 } as const

describe('PortToolsTab', () => {
  it('lists Ping and IPv6 neighbours, Ping first', async () => {
    render(<PortToolsTab port={owned()} selection={sel} readOnly={false} connected />)
    const tabs = screen.getAllByRole('tab')
    expect(tabs.map((t) => t.textContent)).toEqual(['Ping', 'IPv6 neighbours'])
    expect(screen.getByRole('tablist')).toHaveAttribute('aria-orientation', 'vertical')
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('button', { name: 'Ping', hidden: true })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Scan', hidden: true })).not.toBeVisible()   // mounted, hidden
    await userEvent.click(tabs[1])
    expect(screen.getByRole('button', { name: 'Scan', hidden: true })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Ping', hidden: true })).not.toBeVisible()
  })

  it('the gate reason reaches both tools', async () => {
    const { rerender } = render(<PortToolsTab port={owned()} selection={sel} readOnly connected />)
    const ping = screen.getByRole('button', { name: 'Ping', hidden: true }), scan = screen.getByRole('button', { name: 'Scan', hidden: true })
    for (const b of [ping, scan]) { expect(b).toBeDisabled(); expect(b).toHaveAttribute('title', 'read-only session') }
    busy = { 0: 'ping' }
    rerender(<PortToolsTab port={owned()} selection={sel} readOnly={false} connected />)
    expect(screen.getByRole('button', { name: 'Scan', hidden: true })).toHaveAttribute('title', 'busy: pinging')
    busy = {}
    rerender(<PortToolsTab port={owned()} selection={sel} readOnly={false} connected={false} />)
    expect(screen.getByRole('button', { name: 'Ping', hidden: true })).toHaveAttribute('title', 'not connected')
    expect(screen.getByRole('button', { name: 'Scan', hidden: true })).toHaveAttribute('title', 'not connected')
  })
})
