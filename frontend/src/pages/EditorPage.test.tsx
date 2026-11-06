import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import { EditorPage } from './EditorPage'

function Where() { return <span data-testid="where">{useLocation().pathname}</span> }

afterEach(() => vi.restoreAllMocks())

function stub() {
  vi.spyOn(api, 'listLibrary').mockResolvedValue({ profiles: [], load_errors: [], dir: '/lib' })
  vi.spyOn(api, 'packetDefinitions').mockRejectedValue(new Error('no backend here'))
  vi.spyOn(api, 'buildPacket').mockRejectedValue(new Error('no backend here'))
}

describe('EditorPage', () => {
  it('the title is Editor and the selected tab is the tab prop', () => {
    stub()
    render(<MemoryRouter><EditorPage tab="packets" active /></MemoryRouter>)
    expect(screen.getByRole('heading', { name: 'Editor', level: 1 })).toBeVisible()
    expect(screen.getByRole('tab', { name: 'Packets' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'Profiles' })).toHaveAttribute('aria-selected', 'false')
  })
  it('clicking Profiles navigates to /editor, and Packets to /editor/packet', async () => {
    stub()
    render(<MemoryRouter initialEntries={['/editor/packet']}><EditorPage tab="packets" active /><Where /></MemoryRouter>)
    await userEvent.click(screen.getByRole('tab', { name: 'Profiles' }))
    expect(screen.getByTestId('where')).toHaveTextContent(/^\/editor$/)
    await userEvent.click(screen.getByRole('tab', { name: 'Packets' }))
    expect(screen.getByTestId('where')).toHaveTextContent('/editor/packet')
  })
  it('the packet editor stays mounted after a switch back to Profiles', () => {
    stub()
    const { rerender } = render(<MemoryRouter><EditorPage tab="profiles" active /></MemoryRouter>)
    expect(api.packetDefinitions).not.toHaveBeenCalled()                        // not mounted before the first visit
    rerender(<MemoryRouter><EditorPage tab="packets" active /></MemoryRouter>)
    rerender(<MemoryRouter><EditorPage tab="profiles" active /></MemoryRouter>)
    const packet = screen.getByRole('heading', { name: 'Untitled', level: 2, hidden: true })
    expect(packet).toBeInTheDocument()
    expect(packet).not.toBeVisible()
    expect(api.packetDefinitions).toHaveBeenCalledTimes(1)
  })
})
