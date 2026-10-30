import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import * as state from '../../state'
import listing from '../../__fixtures__/captureFiles.json'
import { SpoolFiles } from './SpoolFiles'
import { useSpoolListing } from './useSpoolListing'

const two: api.SpoolListing = { dir: '/var/spool', total: 2, files: [
  listing.listing.files[0] as api.SpoolFile,
  { ...(listing.listing.files[0] as api.SpoolFile), name: 'rec-2-x.partial.pcap', partial: true, pkt_count: 7, capture_id: 2 }] }

function Harness() { return <SpoolFiles spool={useSpoolListing()} /> }

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('SpoolFiles', () => {
  it('shows the directory, files, partial badge and total', async () => {
    vi.spyOn(api, 'captureFiles').mockResolvedValue(two)
    render(<Harness />)
    expect(await screen.findByText('Saved to /var/spool on the backend host')).toBeInTheDocument()
    expect(screen.getByText('rec-1-<stamp>.pcap')).toBeInTheDocument()
    expect(screen.getByText('rec-2-x.partial.pcap')).toBeInTheDocument()
    expect(screen.getAllByText('partial')).toHaveLength(1)
    expect(screen.getByText('120')).toBeInTheDocument()
    expect(screen.getByText('2 files')).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'Download' })[0]).toHaveAttribute('href', api.captureFileUrl('rec-1-<stamp>.pcap'))
  })
  it('Delete confirms and calls the API', async () => {
    const files = vi.spyOn(api, 'captureFiles').mockResolvedValue(two)
    const del = vi.spyOn(api, 'deleteCaptureFile').mockResolvedValue({})
    render(<Harness />)
    await screen.findByText('rec-1-<stamp>.pcap')
    await userEvent.click(screen.getAllByRole('button', { name: 'Delete' })[0])
    expect(del).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Click again to confirm' }))
    expect(del).toHaveBeenCalledWith('rec-1-<stamp>.pcap')
    await vi.waitFor(() => expect(files).toHaveBeenCalledTimes(2))   // the list is read again
  })
  it('reads the list again when a Save finishes', async () => {
    const files = vi.spyOn(api, 'captureFiles').mockResolvedValue(two)
    const spy = vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState })
    const { rerender } = render(<Harness />)
    await screen.findByText('rec-1-<stamp>.pcap')
    spy.mockReturnValue({ ...state.initialState, captureSave: { 1: { type: 'capture_save', id: 1, fetched: 1, total: 1, error: null, file: two.files[0] } } })
    rerender(<Harness />)
    await vi.waitFor(() => expect(files).toHaveBeenCalledTimes(2))
  })
  it('ignores a read that finishes after a newer one started', async () => {
    const resolvers: Array<(l: api.SpoolListing) => void> = []
    vi.spyOn(api, 'captureFiles').mockImplementation(() => new Promise((res) => { resolvers.push(res) }))
    const spy = vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState })
    const { rerender } = render(<Harness />)
    spy.mockReturnValue({ ...state.initialState, captureSave: { 1: { type: 'capture_save', id: 1, fetched: 1, total: 1, error: 'x', file: null } } })
    rerender(<Harness />)
    await vi.waitFor(() => expect(resolvers).toHaveLength(2))
    resolvers[1]({ dir: '/new', files: [], total: 0 })
    await screen.findByText('Saved to /new on the backend host')
    resolvers[0]({ dir: '/old', files: [], total: 0 })
    await new Promise((r) => setTimeout(r, 0))
    expect(screen.getByText('Saved to /new on the backend host')).toBeInTheDocument()
  })
  it('shows a read failure', async () => {
    vi.spyOn(api, 'captureFiles').mockRejectedValue(new Error('spool unavailable'))
    render(<Harness />)
    expect(await screen.findByRole('alert')).toHaveTextContent('spool unavailable')
  })
})
