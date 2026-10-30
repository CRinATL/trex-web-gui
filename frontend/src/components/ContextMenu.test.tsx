import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { MenuAction } from '../portActions'
import { ContextMenu } from './ContextMenu'

afterEach(() => vi.useRealTimers())
const act1 = (o: Partial<MenuAction> & { id: string }): MenuAction => ({ label: o.id, enabled: true, run: () => Promise.resolve(), ...o })

describe('ContextMenu', () => {
  it('arrow keys skip disabled items and Esc closes', async () => {
    const onClose = vi.fn()
    render(<ContextMenu x={0} y={0} onClose={onClose} onError={() => undefined}
      actions={[act1({ id: 'a' }), act1({ id: 'b', enabled: false }), act1({ id: 'c' })]} />)
    expect(screen.getByRole('menuitem', { name: 'b' })).toHaveAttribute('aria-disabled', 'true')
    expect(screen.getByRole('menuitem', { name: 'a' })).toHaveFocus()
    await userEvent.keyboard('{ArrowDown}')
    expect(screen.getByRole('menuitem', { name: 'c' })).toHaveFocus()
    await userEvent.keyboard('{ArrowDown}')
    expect(screen.getByRole('menuitem', { name: 'a' })).toHaveFocus()
    await userEvent.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalled()
  })
  it('Escape closes the menu even when every item is disabled', async () => {
    const onClose = vi.fn()
    render(<ContextMenu x={0} y={0} onClose={onClose} onError={() => undefined} actions={[act1({ id: 'a', enabled: false })]} />)
    expect(screen.getByRole('menu')).toHaveFocus()
    await userEvent.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalled()
  })
  it('menu items are not tab stops', () => {
    render(<ContextMenu x={0} y={0} onClose={() => undefined} onError={() => undefined} actions={[act1({ id: 'a' }), act1({ id: 'b' })]} />)
    for (const i of screen.getAllByRole('menuitem')) expect(i).toHaveAttribute('tabindex', '-1')
  })
  it('a double click runs the command once', async () => {
    let done!: () => void
    const run = vi.fn(() => new Promise<void>((r) => { done = r }))
    render(<ContextMenu x={0} y={0} onClose={() => undefined} onError={() => undefined} actions={[act1({ id: 'a', run })]} />)
    await userEvent.dblClick(screen.getByRole('menuitem', { name: 'a' }))
    expect(run).toHaveBeenCalledTimes(1)
    done()
  })
  it('a request action does not report success', async () => {
    const onError = vi.fn()
    render(<ContextMenu x={0} y={0} onClose={() => undefined} onError={onError} actions={[act1({ id: 'a', request: true })]} />)
    await userEvent.click(screen.getByRole('menuitem', { name: 'a' }))
    expect(onError).not.toHaveBeenCalled()
  })
  it('a disabled item does not run', async () => {
    const run = vi.fn(() => Promise.resolve())
    render(<ContextMenu x={0} y={0} onClose={() => undefined} onError={() => undefined} actions={[act1({ id: 'a', enabled: false, run })]} />)
    await userEvent.click(screen.getByRole('menuitem', { name: 'a' }))
    expect(run).not.toHaveBeenCalled()
  })
  it('a rejection goes to onError and the menu closes; success reports null', async () => {
    const onError = vi.fn(), onClose = vi.fn()
    render(<ContextMenu x={0} y={0} onClose={onClose} onError={onError}
      actions={[act1({ id: 'bad', run: () => Promise.reject(new Error('boom')) }), act1({ id: 'ok' })]} />)
    await userEvent.click(screen.getByRole('menuitem', { name: 'bad' }))
    expect(onError).toHaveBeenLastCalledWith('boom')
    expect(onClose).toHaveBeenCalledTimes(1)
    await userEvent.click(screen.getByRole('menuitem', { name: 'ok' }))
    expect(onError).toHaveBeenLastCalledWith(null)
  })
  it('a danger item needs a second click within 5 s', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const run = vi.fn(() => Promise.resolve())
    render(<ContextMenu x={0} y={0} onClose={() => undefined} onError={() => undefined} actions={[act1({ id: 'reset', label: 'Reset', danger: true, run })]} />)
    await userEvent.click(screen.getByRole('menuitem', { name: 'Reset' }))
    expect(run).not.toHaveBeenCalled()
    expect(screen.getByRole('menuitem')).toHaveTextContent('Click again to confirm')
    act(() => { vi.advanceTimersByTime(5100) })
    expect(screen.getByRole('menuitem')).toHaveTextContent('Reset')
    await userEvent.click(screen.getByRole('menuitem'))
    await userEvent.click(screen.getByRole('menuitem'))
    expect(run).toHaveBeenCalledTimes(1)
  })
  it('a danger item with confirm text shows it while confirming', async () => {
    render(<ContextMenu x={0} y={0} onClose={() => undefined} onError={() => undefined}
      actions={[act1({ id: 'reset', label: 'Reset', danger: true, confirm: 'Captures 3, 5 will be removed from the server (7 unsaved packets)' })]} />)
    await userEvent.click(screen.getByRole('menuitem', { name: 'Reset' }))
    expect(screen.getByRole('menuitem')).toHaveTextContent('Captures 3, 5 will be removed from the server (7 unsaved packets)')
    expect(screen.getByRole('menuitem')).toHaveTextContent('Click again to confirm')
  })
  it('returns focus to the opener when it unmounts', () => {
    const row = document.createElement('div'); row.tabIndex = 0; document.body.append(row); row.focus()
    const { unmount } = render(<ContextMenu x={0} y={0} onClose={() => undefined} onError={() => undefined} actions={[act1({ id: 'a' })]} />)
    expect(screen.getByRole('menuitem')).toHaveFocus()
    unmount()
    expect(row).toHaveFocus()
    row.remove()
  })
})
