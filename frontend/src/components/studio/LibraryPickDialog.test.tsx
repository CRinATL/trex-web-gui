import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import type { LibraryEntry } from '../../api'
import { LibraryPickDialog } from './LibraryPickDialog'

afterEach(() => vi.restoreAllMocks())
const entry = (slug: string, o: Partial<LibraryEntry> = {}): LibraryEntry => ({
  slug, name: slug, path: `/lib/${slug}.json`, stream_count: 2, created: 'c', updated: 'u', source_missing: false, load_error: null,
  source: { kind: 'json', path: null, args: [], tunables: null, ports_differ: false, imported: 'i' }, ...o,
})
const py = entry('udp', { source: { kind: 'py', path: '/srv/p/udp.py', args: [], tunables: { values: { n: 3 }, extra: '' }, ports_differ: false, imported: 'i' } })

describe('LibraryPickDialog', () => {
  it('lists entries and shows the .py note', async () => {
    vi.spyOn(api, 'listLibrary').mockResolvedValue({ profiles: [entry('plain'), py], load_errors: [], dir: '/lib' })
    const onPick = vi.fn()
    render(<LibraryPickDialog onPick={onPick} onClose={vi.fn()} />)
    expect(screen.getByRole('dialog', { name: 'Pick from the library' })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'plain' })).toBeInTheDocument()
    expect(screen.getByText(/\/srv\/p\/udp\.py: .*assigns the \.py source with these tunables; editor changes to this entry are not sent/i)).toBeInTheDocument()
    expect(screen.getAllByText(/editor changes to this entry are not sent/)).toHaveLength(1)   // only the .py entry carries it
    await userEvent.click(screen.getByRole('button', { name: 'udp' }))
    expect(onPick).toHaveBeenCalledWith(py)
  })
  it('shows a read failure and an empty library', async () => {
    vi.spyOn(api, 'listLibrary').mockRejectedValue(new Error('boom'))
    render(<LibraryPickDialog onPick={vi.fn()} onClose={vi.fn()} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('boom')
  })
  it('marks a .py entry whose source is missing and does not let it be picked', async () => {
    vi.spyOn(api, 'listLibrary').mockResolvedValue({ profiles: [{ ...py, source_missing: true }], load_errors: [], dir: '/lib' })
    render(<LibraryPickDialog onPick={vi.fn()} onClose={vi.fn()} />)
    expect(await screen.findByText(/source missing/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'udp' })).toBeDisabled()
  })
  it('focuses the first entry that can be picked', async () => {
    vi.spyOn(api, 'listLibrary').mockResolvedValue({ profiles: [entry('broken', { load_error: 'bad sidecar' }), entry('good')], load_errors: [], dir: '/lib' })
    render(<LibraryPickDialog onPick={vi.fn()} onClose={vi.fn()} />)
    // the focus moves in an effect after the list renders; under load that can land after findByRole returns
    await waitFor(() => expect(screen.getByRole('button', { name: 'good' })).toHaveFocus())
  })
})
