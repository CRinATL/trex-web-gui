import { render, screen } from '@testing-library/react'
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
