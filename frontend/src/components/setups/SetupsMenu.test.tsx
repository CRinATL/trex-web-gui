import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { SetupsMenu } from './SetupsMenu'

describe('SetupsMenu', () => {
  const open = async () => {
    const h = { onSave: vi.fn(), onManage: vi.fn() }
    render(<div><SetupsMenu connected onSave={h.onSave} onManage={h.onManage} /><p>elsewhere</p></div>)
    await userEvent.click(screen.getByRole('button', { name: 'Setups ▾' }))
    expect(screen.getByRole('menu')).toBeInTheDocument()
    return h
  }
  it('closes on an outside click', async () => {
    await open()
    await userEvent.click(screen.getByText('elsewhere'))
    expect(screen.queryByRole('menu')).toBeNull()
  })
  it('closes on Escape', async () => {
    await open()
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('menu')).toBeNull()
  })
  it('a pick calls its handler and closes the menu', async () => {
    const h = await open()
    await userEvent.click(screen.getByRole('menuitem', { name: 'Manage setups…' }))
    expect(h.onManage).toHaveBeenCalled()
    expect(screen.queryByRole('menu')).toBeNull()
  })
})
