import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { HexView } from './HexView'

afterEach(cleanup)
const frame = Uint8Array.from({ length: 40 }, (_, i) => 0x41 + (i % 26))

describe('HexView', () => {
  it('renders ceil(n/16) rows with offsets', () => {
    const { container } = render(<HexView bytes={frame} selected={null} onSelectByte={() => undefined} />)
    const rows = container.querySelectorAll('.hex-row')
    expect(rows).toHaveLength(3)
    expect(Array.from(rows, (r) => r.querySelector('.hex-offset')?.textContent)).toEqual(['0000', '0010', '0020'])
    expect(rows[2].querySelectorAll('.hex-byte')).toHaveLength(8)
    expect(rows[0].querySelector('.hex-ascii')?.textContent).toBe('ABCDEFGHIJKLMNOP')
  })
  it('the selected range is marked', () => {
    render(<HexView bytes={frame} selected={[14, 18]} onSelectByte={() => undefined} />)
    const marked = screen.getAllByRole('button', { pressed: true }).map((b) => b.getAttribute('aria-label'))
    expect(marked).toEqual(['byte 14: 0x4f', 'byte 15: 0x50', 'byte 16: 0x51', 'byte 17: 0x52'])   // across a row break; the end is exclusive
  })
  it('clicking a byte reports its index', async () => {
    const onSelectByte = vi.fn()
    render(<HexView bytes={frame} selected={null} onSelectByte={onSelectByte} />)
    await userEvent.click(screen.getByRole('button', { name: 'byte 17: 0x52' }))
    expect(onSelectByte).toHaveBeenCalledWith(17)
  })
  it('is one tab stop: arrow keys move between the bytes, Enter selects', async () => {
    const onSelectByte = vi.fn()
    render(<HexView bytes={frame} selected={null} onSelectByte={onSelectByte} />)
    const stops = () => screen.getAllByRole('button').filter((b) => b.tabIndex === 0)
    expect(stops()).toHaveLength(1)
    await userEvent.tab()
    expect(screen.getByRole('button', { name: 'byte 0: 0x41' })).toHaveFocus()
    await userEvent.keyboard('{ArrowRight}{ArrowDown}')
    expect(screen.getByRole('button', { name: 'byte 17: 0x52' })).toHaveFocus()
    expect(stops().map((b) => b.getAttribute('aria-label'))).toEqual(['byte 17: 0x52'])
    await userEvent.keyboard('{End}')
    expect(screen.getByRole('button', { name: 'byte 39: 0x4e' })).toHaveFocus()
    await userEvent.keyboard('{Home}{Enter}')
    expect(onSelectByte).toHaveBeenLastCalledWith(0)
    await userEvent.keyboard('{ArrowLeft}')   // clamped
    expect(screen.getByRole('button', { name: 'byte 0: 0x41' })).toHaveFocus()
  })
  it('renders a 9238-byte packet as at most 60 row nodes', () => {
    const big = Uint8Array.from({ length: 9238 }, (_, i) => i & 0xff)
    const { container } = render(<HexView bytes={big} selected={null} onSelectByte={() => undefined} />)
    const rows = container.querySelectorAll('.hex-row').length
    expect(rows).toBeGreaterThan(20)
    expect(rows).toBeLessThanOrEqual(60)
    expect(container.querySelectorAll('.hex-byte').length).toBeLessThanOrEqual(60 * 16)
  })
  it('scrolling renders the rows in view', () => {
    const big = Uint8Array.from({ length: 9238 }, (_, i) => i & 0xff)
    const { container } = render(<HexView bytes={big} selected={null} onSelectByte={() => undefined} rowHeight={20} height={400} />)
    const offsets = () => Array.from(container.querySelectorAll('.hex-offset'), (e) => e.textContent)
    expect(offsets()).toContain('0000')
    expect(offsets()).not.toContain('1000')
    const view = container.querySelector('.hex-view') as HTMLElement
    view.scrollTop = 256 * 20   // row 256 is offset 0x1000
    fireEvent.scroll(view)
    expect(offsets()).toContain('1000')
    expect(offsets()).not.toContain('0010')   // row 0 stays only because it holds the tab stop
    expect(offsets().length).toBeLessThanOrEqual(60)
    expect(screen.getByRole('button', { name: 'byte 4096: 0x00' })).toBeInTheDocument()
  })
  it('the tab stop stays reachable while its row is scrolled out of view', () => {
    const big = Uint8Array.from({ length: 9238 }, (_, i) => i & 0xff)
    const { container } = render(<HexView bytes={big} selected={null} onSelectByte={() => undefined} />)
    const view = container.querySelector('.hex-view') as HTMLElement
    view.scrollTop = 400 * 20
    fireEvent.scroll(view)
    expect(screen.getAllByRole('button').filter((b) => b.tabIndex === 0).map((b) => b.getAttribute('aria-label'))).toEqual(['byte 0: 0x00'])
  })
  it('arrow keys scroll an unrendered byte into view and focus it', async () => {
    const big = Uint8Array.from({ length: 9238 }, (_, i) => i & 0xff)
    render(<HexView bytes={big} selected={null} onSelectByte={() => undefined} />)
    await userEvent.tab()
    await userEvent.keyboard('{End}')
    expect(screen.getByRole('button', { name: `byte 9237: 0x${(9237 & 0xff).toString(16)}` })).toHaveFocus()
  })
  it('selection marks bytes across rows', () => {
    const { container } = render(<HexView bytes={frame} selected={[14, 34]} onSelectByte={() => undefined} />)
    const rows = Array.from(container.querySelectorAll('.hex-row'))
    expect(rows.map((r) => r.querySelectorAll('.hex-byte.marked').length)).toEqual([2, 16, 2])
    expect(rows.map((r) => r.querySelectorAll('.hex-ascii .marked').length)).toEqual([2, 16, 2])
  })
  it('typing two hex digits edits the focused byte', async () => {
    const onEditByte = vi.fn()
    render(<HexView bytes={frame} selected={null} onSelectByte={() => undefined} onEditByte={onEditByte} />)
    await userEvent.click(screen.getByRole('button', { name: 'byte 3: 0x44' }))
    await userEvent.keyboard('a')
    expect(onEditByte).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'byte 3: 0x44' })).toHaveTextContent('a_')
    await userEvent.keyboard('F')
    expect(onEditByte).toHaveBeenCalledExactlyOnceWith(3, 0xaf)
    await userEvent.keyboard('x')   // not a hex digit
    await userEvent.keyboard('{Control>}c{/Control}')
    expect(onEditByte).toHaveBeenCalledTimes(1)
  })
  it('Escape cancels a half-typed byte', async () => {
    const onEditByte = vi.fn()
    render(<HexView bytes={frame} selected={null} onSelectByte={() => undefined} onEditByte={onEditByte} />)
    await userEvent.click(screen.getByRole('button', { name: 'byte 3: 0x44' }))
    await userEvent.keyboard('a{Escape}')
    expect(screen.getByRole('button', { name: 'byte 3: 0x44' })).toHaveTextContent('44')
    await userEvent.keyboard('b')   // a fresh first digit, not the second of the cancelled byte
    expect(onEditByte).not.toHaveBeenCalled()
    await userEvent.keyboard('c')
    expect(onEditByte).toHaveBeenCalledExactlyOnceWith(3, 0xbc)
  })
  it('without onEditByte typing digits does nothing', async () => {
    render(<HexView bytes={frame} selected={null} onSelectByte={() => undefined} />)
    await userEvent.click(screen.getByRole('button', { name: 'byte 3: 0x44' }))
    await userEvent.keyboard('ab')
    expect(screen.getByRole('button', { name: 'byte 3: 0x44' })).toHaveTextContent('44')
  })
  it('rows are in normal flow, so the view takes the width of a row in an auto grid column (not zero)', () => {
    const big = Uint8Array.from({ length: 9238 }, (_, i) => i & 0xff)
    const { container } = render(<HexView bytes={big} selected={null} onSelectByte={() => undefined} />)
    const view = container.querySelector('.hex-view') as HTMLElement
    expect(view.querySelectorAll('[style*="absolute"]')).toHaveLength(0)
    const spacers = Array.from(view.children).filter((c) => !c.classList.contains('hex-row')) as HTMLElement[]
    expect(spacers.length).toBeGreaterThan(0)
    // the rows and spacers add up to the whole frame, so the scroll bar is the frame's height
    const sum = Array.from(view.children).reduce((n, c) => n + parseFloat((c as HTMLElement).style.height), 0)
    expect(sum).toBe(Math.ceil(9238 / 16) * 20)
  })
  it('a short frame leaves no blank space: the view is as tall as its rows', () => {
    const { container } = render(<HexView bytes={frame} selected={null} onSelectByte={() => undefined} />)
    expect((container.querySelector('.hex-view') as HTMLElement).style.height).toBe('60px')
  })
  it('reserves the scroll bar gutter, so the bar never clips the ASCII column', () => {
    const big = Uint8Array.from({ length: 9238 }, (_, i) => i & 0xff)
    const { container } = render(<HexView bytes={big} selected={null} onSelectByte={() => undefined} />)
    expect((container.querySelector('.hex-view') as HTMLElement).style.scrollbarGutter).toBe('stable')
  })
})
