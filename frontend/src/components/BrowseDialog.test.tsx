import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import { BrowseDialog } from './BrowseDialog'
import { sizeText } from './sizeText'

const listing = (n: number): api.FsList => ({
  path: '/home/u', parent: '/home', truncated: false,
  entries: [{ name: 'caps', dir: true, size: null }, ...Array.from({ length: n }, (_, i) => ({ name: `run-${i}.pcap`, dir: false, size: 12596 + i }))],
})

afterEach(() => { vi.restoreAllMocks(); localStorage.clear() })

describe('BrowseDialog', () => {
  it('renders into <body>, not inside the dialog that opened it', async () => {
    vi.spyOn(api, 'fsList').mockResolvedValue(listing(3))
    const { container } = render(<div className="modal"><BrowseDialog pcap onPick={vi.fn()} onClose={vi.fn()} /></div>)
    const dialog = await screen.findByRole('dialog', { name: 'Browse for a profile' })
    expect(container.contains(dialog)).toBe(false)
    expect(dialog.parentElement).toHaveClass('modal-backdrop')
    expect(dialog.parentElement!.parentElement).toBe(document.body)
  })
  it('the filter narrows a long list and says how many match; a file picks its full path', async () => {
    vi.spyOn(api, 'fsList').mockResolvedValue(listing(120))
    const onPick = vi.fn()
    render(<BrowseDialog pcap onPick={onPick} onClose={vi.fn()} />)
    await screen.findByRole('button', { name: 'run-119.pcap' })
    expect(screen.getByRole('textbox', { name: 'Filter' })).toHaveFocus()
    await userEvent.type(screen.getByRole('textbox', { name: 'Filter' }), 'run-11')
    expect(screen.getAllByRole('listitem')).toHaveLength(11)     // run-11 and run-110 to run-119
    expect(screen.getByText('11 of 121')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'run-110.pcap' }))
    expect(onPick).toHaveBeenCalledWith('/home/u/run-110.pcap')
    await userEvent.clear(screen.getByRole('textbox', { name: 'Filter' }))
    await userEvent.type(screen.getByRole('textbox', { name: 'Filter' }), 'nothing')
    expect(screen.getByText('Nothing here matches the filter.')).toBeInTheDocument()
  })
  it('sizes read as people read them', () => {
    expect(sizeText(0)).toBe('0 B')
    expect(sizeText(1023)).toBe('1023 B')
    expect(sizeText(12596)).toBe('12.3 KiB')
    expect(sizeText(5 * 1024 * 1024)).toBe('5.0 MiB')
    expect(sizeText(3 * 1024 ** 3)).toBe('3.0 GiB')
  })
})
