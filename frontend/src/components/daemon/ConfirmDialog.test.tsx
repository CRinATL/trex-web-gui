import { act, fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ConfirmDialog } from './ConfirmDialog'

const show = (danger?: boolean) => render(
  <ConfirmDialog title="Force stop TRex" body={<p>x</p>} confirmLabel="Force stop" danger={danger}
    onConfirm={vi.fn(async () => {})} onClose={vi.fn()} />)

describe('ConfirmDialog', () => {
  it('styles the confirm button as destructive with danger, and only then (UI-14)', () => {
    const { unmount } = show(true)
    expect(screen.getByRole('button', { name: 'Force stop' })).toHaveClass('danger')
    expect(screen.getByRole('button', { name: 'Cancel' })).not.toHaveClass('danger')
    unmount()
    show()
    expect(screen.getByRole('button', { name: 'Force stop' })).not.toHaveClass('danger')
  })
})

describe('ConfirmDialog progress', () => {
  it('while the action runs, shows the progress line and counts elapsed seconds against the limit', async () => {
    vi.useFakeTimers()
    let finish: () => void = () => undefined
    const onConfirm = vi.fn(() => new Promise<void>((r) => { finish = r }))
    render(<ConfirmDialog title="Start TRex" body={<p>the command</p>} confirmLabel="Start" onConfirm={onConfirm} onClose={vi.fn()}
      progress={{ label: 'Starting TRex', busyLabel: 'Starting…', limitS: 40, detail: <p>detail text</p> }} />)
    expect(screen.queryByRole('progressbar')).toBeNull()
    act(() => { fireEvent.click(screen.getByRole('button', { name: 'Start' })) })
    const bar = screen.getByRole('progressbar', { name: 'Starting TRex' })
    expect(bar).toHaveAttribute('max', '40')
    expect(bar).toHaveAttribute('value', '0')
    expect(screen.getByText('detail text')).toBeInTheDocument()
    expect(screen.getByRole('timer')).toHaveTextContent('0:00 of up to 0:40')
    expect(screen.getByRole('button', { name: 'Starting…' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
    act(() => { vi.advanceTimersByTime(12_000) })
    expect(screen.getByRole('timer')).toHaveTextContent('0:12 of up to 0:40')
    expect(bar).toHaveAttribute('value', '12')
    act(() => { vi.advanceTimersByTime(60_000) })   // past the limit: the bar stays full, the count goes on
    expect(screen.getByRole('timer')).toHaveTextContent('1:12 of up to 0:40')
    expect(bar).toHaveAttribute('value', '40')
    await act(async () => { finish() })
    expect(screen.queryByRole('progressbar')).toBeNull()
    vi.useRealTimers()
  })

  it('a failed action leaves the progress view and shows the error over the body again', async () => {
    const onConfirm = vi.fn(async () => { throw new Error('bad config') })
    render(<ConfirmDialog title="Start TRex" body={<p>the command</p>} confirmLabel="Start" onConfirm={onConfirm} onClose={vi.fn()}
      progress={{ label: 'Starting TRex', busyLabel: 'Starting…', limitS: 40 }} />)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Start' })) })
    expect(screen.queryByRole('progressbar')).toBeNull()
    expect(screen.getByRole('alert')).toHaveTextContent('bad config')
    expect(screen.getByText('the command')).toBeInTheDocument()
  })
})
