import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { DecodedRow } from '../../api'
import capturePkts from '../../__fixtures__/capturePkts.json'
import { PacketTable } from './PacketTable'

const base = capturePkts.batch.rows[0] as DecodedRow
const rows = (from: number, n: number): DecodedRow[] => Array.from({ length: n }, (_, i) => ({ ...base, index: from + i }))
const bodyRows = () => screen.getAllByRole('row').slice(1)

afterEach(() => { vi.restoreAllMocks(); delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView })

describe('PacketTable', () => {
  it('renders the nine columns from capturePkts.json', () => {
    render(<PacketTable rows={capturePkts.batch.rows as DecodedRow[]} onOpen={() => undefined} empty="none" />)
    expect(screen.getAllByRole('columnheader').map((h) => h.textContent)).toEqual(['No.', 'Time', 'Port', 'Mode', 'Src', 'Dst', 'Type', 'Len', 'Info'])
    const first = within(bodyRows()[0]).getAllByRole('gridcell').map((c) => c.textContent)
    expect(first).toEqual(['901', '0.901000', '1', 'RX', '16.0.0.1', '48.0.0.1', 'UDP', '124', 'Ether / IP / UDP 16.0.0.1:1025 > 48.0.0.1:12 / Raw'])
    expect(bodyRows()).toHaveLength(50)
    expect(screen.getByRole('grid')).toHaveAttribute('aria-rowcount', '51')
  })
  it('pages at 100 and follows the newest page', () => {
    const { rerender } = render(<PacketTable rows={rows(1, 250)} onOpen={() => undefined} empty="none" />)
    expect(bodyRows()).toHaveLength(50)
    expect(within(bodyRows()[0]).getAllByRole('gridcell')[0]).toHaveTextContent('201')
    rerender(<PacketTable rows={rows(1, 320)} onOpen={() => undefined} empty="none" />)
    expect(bodyRows()).toHaveLength(20)
    expect(within(bodyRows()[0]).getAllByRole('gridcell')[0]).toHaveTextContent('301')
  })
  it('stays on the page the user chose', async () => {
    const { rerender } = render(<PacketTable rows={rows(1, 250)} onOpen={() => undefined} empty="none" />)
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Page' }), '1')
    expect(within(bodyRows()[0]).getAllByRole('gridcell')[0]).toHaveTextContent('1')
    rerender(<PacketTable rows={rows(1, 320)} onOpen={() => undefined} empty="none" />)
    expect(bodyRows()).toHaveLength(100)
    expect(within(bodyRows()[0]).getAllByRole('gridcell')[0]).toHaveTextContent('1')
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Page' }), '4')   // the newest page: following again
    rerender(<PacketTable rows={rows(1, 420)} onOpen={() => undefined} empty="none" />)
    expect(within(bodyRows()[0]).getAllByRole('gridcell')[0]).toHaveTextContent('401')
  })
  it('arrow keys move the selection', async () => {
    render(<PacketTable rows={rows(1, 5)} onOpen={() => undefined} empty="none" />)
    screen.getByRole('grid').focus()
    await userEvent.keyboard('{ArrowDown}')
    expect(bodyRows()[0]).toHaveAttribute('aria-selected', 'true')
    await userEvent.keyboard('{ArrowDown}{ArrowDown}')
    expect(bodyRows()[2]).toHaveAttribute('aria-selected', 'true')
    expect(bodyRows()[0]).toHaveAttribute('aria-selected', 'false')
    await userEvent.keyboard('{ArrowUp}')
    expect(bodyRows()[1]).toHaveAttribute('aria-selected', 'true')
    await userEvent.keyboard('{ArrowUp}{ArrowUp}{ArrowUp}')
    expect(bodyRows()[0]).toHaveAttribute('aria-selected', 'true')
  })
  it('is a grid, so the selection is exposed', () => {
    render(<PacketTable rows={rows(1, 3)} onOpen={() => undefined} empty="none" />)
    expect(screen.getByRole('grid')).toHaveAttribute('aria-rowcount', '4')
    expect(within(bodyRows()[0]).getAllByRole('gridcell')).toHaveLength(9)
  })
  it('arrow keys at a page edge move to the adjacent page and scroll the row into view', async () => {
    const scroll = vi.fn()
    // jsdom has no scrollIntoView, so there is nothing to spyOn: define it; afterEach deletes it.
    Object.defineProperty(Element.prototype, 'scrollIntoView', { value: scroll, configurable: true, writable: true })
    render(<PacketTable rows={rows(1, 250)} onOpen={() => undefined} empty="none" />)   // following the newest page (201-250)
    screen.getByRole('grid').focus()
    await userEvent.keyboard('{ArrowUp}')                                  // nothing selected: the last row, 250
    for (let i = 0; i < 49; i++) await userEvent.keyboard('{ArrowUp}')     // 201
    expect(bodyRows()[0]).toHaveAttribute('aria-selected', 'true')
    await userEvent.keyboard('{ArrowUp}')                                  // 200: the previous page
    expect(bodyRows()).toHaveLength(100)
    expect(within(bodyRows()[99]).getAllByRole('gridcell')[0]).toHaveTextContent('200')
    expect(bodyRows()[99]).toHaveAttribute('aria-selected', 'true')
    expect(scroll).toHaveBeenCalled()
    await userEvent.keyboard('{ArrowDown}')                                // 201: back on the last page
    expect(within(bodyRows()[0]).getAllByRole('gridcell')[0]).toHaveTextContent('201')
    expect(bodyRows()[0]).toHaveAttribute('aria-selected', 'true')
  })
  it('the grid names the selected row with aria-activedescendant, and drops it when that row is off the page', async () => {
    render(<PacketTable rows={rows(1, 250)} onOpen={() => undefined} empty="none" />)
    const grid = screen.getByRole('grid')
    expect(grid).not.toHaveAttribute('aria-activedescendant')
    grid.focus()
    await userEvent.keyboard('{ArrowDown}')                       // the first row of the newest page: 201
    expect(grid).toHaveAttribute('aria-activedescendant', 'pkt-201')
    expect(document.getElementById('pkt-201')).toBe(bodyRows()[0])
    await userEvent.keyboard('{ArrowDown}')
    expect(grid).toHaveAttribute('aria-activedescendant', 'pkt-202')
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Page' }), '0')
    expect(grid).not.toHaveAttribute('aria-activedescendant')     // 202 is not on page 1
  })
  it('Enter does nothing when the selected row is on another page', async () => {
    const onOpen = vi.fn()
    render(<PacketTable rows={rows(1, 250)} onOpen={onOpen} empty="none" />)
    screen.getByRole('grid').focus()
    await userEvent.keyboard('{ArrowDown}')                       // 201
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Page' }), '0')
    screen.getByRole('grid').focus()
    await userEvent.keyboard('{Enter}')
    expect(onOpen).not.toHaveBeenCalled()
  })
  it('Enter opens the selected row, and so does a double click', async () => {
    const onOpen = vi.fn()
    render(<PacketTable rows={rows(1, 5)} onOpen={onOpen} empty="none" />)
    screen.getByRole('grid').focus()
    await userEvent.keyboard('{Enter}')
    expect(onOpen).not.toHaveBeenCalled()
    await userEvent.keyboard('{ArrowDown}{ArrowDown}{Enter}')
    expect(onOpen).toHaveBeenCalledTimes(1)
    expect(onOpen.mock.calls[0][0].index).toBe(2)
    fireEvent.doubleClick(bodyRows()[3])
    expect(onOpen.mock.calls[1][0].index).toBe(4)
  })
  it('an empty table says why in one row under its header', () => {
    render(<PacketTable rows={[]} onOpen={() => undefined} empty="No packets. Start the monitor to capture." />)
    const body = screen.getAllByRole('row')
    expect(body).toHaveLength(2)
    expect(screen.getByRole('gridcell')).toHaveTextContent('No packets. Start the monitor to capture.')
    expect(screen.getByRole('gridcell')).toHaveAttribute('colspan', '9')
  })
})
