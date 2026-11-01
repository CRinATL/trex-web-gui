import { act, cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import * as state from '../../state'
import { Recorders } from './Recorders'
import type { SpoolSource } from './useSpoolListing'

const session = (o: Partial<api.SessionSnapshot> = {}): api.SessionSnapshot =>
  ({ type: 'session', state: 'connected', params: null, info: null, error: null, rpc_console: false, include_publishing: false, ...o } as unknown as api.SessionSnapshot)
const row = (o: Partial<api.CaptureRow>): api.CaptureRow => ({ id: 1, state: 'STOPPED', mode: 'fixed', tx: [], rx: [1], bpf: '', count: 0, matched: 0, fetched: 0, limit: 100, bytes: 0, endpoint: null, mine: true, service: false, ...o })
const file = (o: Partial<api.SpoolFile> = {}): api.SpoolFile => ({ name: 'rec-1-x.pcap', size: 100, mtime: 0, capture_id: 1, server: 's', start_ts: 0, pkt_count: 5, partial: false, ...o })
const save = (o: Partial<api.CaptureSaveMsg>): api.CaptureSaveMsg => ({ type: 'capture_save', id: 1, fetched: 0, total: 0, file: null, error: null, ...o })
const mockState = (captures: api.CaptureRow[], o: Partial<state.AppState> = {}, monitor: api.MonitorState | null = null) => vi.spyOn(state, 'useAppState').mockReturnValue({
  ...state.initialState, session: session(), capture: { ...state.initialState.capture, captures, monitor }, ...o })
const params = { host: 'h', sync_port: 4501, async_port: 4500, timeout_s: 3 }
const spool = (files: api.SpoolFile[] = []): SpoolSource => ({ listing: { dir: '/d', files, total: files.length }, error: null, reload: () => undefined })
const rowOf = (id: number) => screen.getByRole('row', { name: new RegExp(`^${id} `) })

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('Recorders', () => {
  it('lists other and TRex service rows; service rows have Remove disabled', () => {
    mockState([row({ id: 1, mine: true }), row({ id: 2, mine: false }), row({ id: 3, mine: false, service: true })])
    render(<Recorders spool={spool()} />)
    expect(within(rowOf(1)).getByText('this GUI')).toBeInTheDocument()
    expect(within(rowOf(2)).getByText('other')).toBeInTheDocument()
    expect(within(rowOf(3)).getByText('TRex service')).toBeInTheDocument()
    expect(within(rowOf(3)).getByRole('button', { name: 'Remove' })).toBeDisabled()
    expect(within(rowOf(2)).getByRole('button', { name: 'Remove' })).toBeEnabled()
  })
  it('Stop is offered for an ACTIVE capture only', async () => {
    mockState([row({ id: 1, state: 'ACTIVE' }), row({ id: 2, state: 'STOPPED' })])
    const stop = vi.spyOn(api, 'recorderStop').mockResolvedValue({ pkt_count: 0 })
    render(<Recorders spool={spool()} />)
    expect(within(rowOf(2)).queryByRole('button', { name: 'Stop' })).toBeNull()
    await userEvent.click(within(rowOf(1)).getByRole('button', { name: 'Stop' }))
    expect(stop).toHaveBeenCalledWith(1)
  })
  it('Save shows progress, then no second Save once the file is complete', async () => {
    const r = [row({ id: 1, count: 120, limit: 120 })]
    mockState(r, { captureSave: { 1: save({ fetched: 50, total: 120 }) } })
    const saveCall = vi.spyOn(api, 'recorderSave').mockResolvedValue({ file: file() })
    const { rerender } = render(<Recorders spool={spool()} />)
    expect(screen.getByText('fetched 50 of 120')).toBeInTheDocument()
    expect(within(rowOf(1)).getByRole('button', { name: 'Remove' })).toBeDisabled()   // during a Save
    mockState(r, { captureSave: { 1: save({ fetched: 120, total: 120, file: file({ name: 'rec-1-done.pcap', pkt_count: 120 }) }) } })
    rerender(<Recorders spool={spool()} />)
    expect(screen.queryByText(/fetched 120 of/)).toBeNull()
    expect(within(rowOf(1)).queryByRole('button', { name: 'Save' })).toBeNull()   // a second Save would write an empty file
    expect(saveCall).not.toHaveBeenCalled()
  })
  it('Save sends the id', async () => {
    mockState([row({ id: 4, count: 3 })])
    const saveCall = vi.spyOn(api, 'recorderSave').mockResolvedValue({ file: file() })
    render(<Recorders spool={spool()} />)
    await userEvent.click(within(rowOf(4)).getByRole('button', { name: 'Save' }))
    expect(saveCall).toHaveBeenCalledWith(4)
  })
  it('a partial file in the listing offers Resume with nothing in captureSave', async () => {
    mockState([row({ id: 1, count: 20 })], { session: session({ params }) })
    const saveCall = vi.spyOn(api, 'recorderSave').mockResolvedValue({ file: file() })
    render(<Recorders spool={spool([file({ name: 'rec-1-x.partial.pcap', partial: true, server: 'h:4501', capture_id: 1 })])} />)
    expect(within(rowOf(1)).queryByRole('button', { name: 'Save' })).toBeNull()
    await userEvent.click(within(rowOf(1)).getByRole('button', { name: 'Resume' }))
    expect(saveCall).toHaveBeenCalledWith(1)
  })
  it('a partial file of another server or capture does not offer Resume', () => {
    mockState([row({ id: 1, count: 20 }), row({ id: 2, count: 20 })], { session: session({ params }) })
    render(<Recorders spool={spool([
      file({ partial: true, server: 'other:4501', capture_id: 1 }), file({ name: 'b', partial: true, server: 'h:4501', capture_id: 9 })])} />)
    expect(within(rowOf(1)).getByRole('button', { name: 'Save' })).toBeInTheDocument()
    expect(within(rowOf(2)).getByRole('button', { name: 'Save' })).toBeInTheDocument()
  })
  it('a save error offers Resume and shows the error and the note', () => {
    mockState([row({ id: 1, count: 20 })], { captureSave: { 1: save({ error: 'connection lost', note: 'a partial file from another capture was left in place' }) } })
    render(<Recorders spool={spool()} />)
    expect(screen.getByRole('alert')).toHaveTextContent('connection lost')
    expect(screen.getByText('a partial file from another capture was left in place')).toBeInTheDocument()
    expect(within(rowOf(1)).getByRole('button', { name: 'Resume' })).toBeInTheDocument()
  })
  it('Remove of an unsaved stopped capture confirms first', async () => {
    mockState([row({ id: 1, count: 7 })])
    const remove = vi.spyOn(api, 'recorderRemove').mockResolvedValue({})
    render(<Recorders spool={spool()} />)
    await userEvent.click(within(rowOf(1)).getByRole('button', { name: 'Remove' }))
    expect(remove).not.toHaveBeenCalled()
    await userEvent.click(within(rowOf(1)).getByRole('button', { name: /7 packets will be lost\. Remove\?/ }))
    expect(remove).toHaveBeenCalledWith(1)
  })
  it('Remove of an ACTIVE capture with packets confirms first', async () => {
    mockState([row({ id: 1, state: 'ACTIVE', count: 900 })])
    const remove = vi.spyOn(api, 'recorderRemove').mockResolvedValue({})
    render(<Recorders spool={spool()} />)
    await userEvent.click(within(rowOf(1)).getByRole('button', { name: 'Remove' }))
    expect(remove).not.toHaveBeenCalled()
    await userEvent.click(within(rowOf(1)).getByRole('button', { name: /900 packets will be lost\. Remove\?/ }))
    expect(remove).toHaveBeenCalledWith(1)
  })
  it('Remove of an empty or saved capture needs no confirmation', async () => {
    mockState([row({ id: 1, count: 0 }), row({ id: 2, count: 5 })], { captureSave: { 2: save({ id: 2, file: file({ capture_id: 2 }) }) } })
    const remove = vi.spyOn(api, 'recorderRemove').mockResolvedValue({})
    render(<Recorders spool={spool()} />)
    await userEvent.click(within(rowOf(1)).getByRole('button', { name: 'Remove' }))
    await userEvent.click(within(rowOf(2)).getByRole('button', { name: 'Remove' }))
    expect(remove.mock.calls).toEqual([[1], [2]])
  })
  it('endpoint rows have no Save or Stop', () => {
    mockState([row({ id: 1, state: 'ACTIVE', endpoint: 'ipv4 10.0.0.1' })])
    render(<Recorders spool={spool()} />)
    expect(within(rowOf(1)).getByText('endpoint ipv4 10.0.0.1')).toBeInTheDocument()
    expect(within(rowOf(1)).queryByRole('button', { name: 'Save' })).toBeNull()
    expect(within(rowOf(1)).queryByRole('button', { name: 'Stop' })).toBeNull()
  })
  it('the monitor row has no Stop, Save or Remove', () => {
    const monitor = { id: 1, start_ts: 0, tx: [], rx: [1], bpf: '', limit: 100, fetched: 0, matched: 0, count: 0, state: 'ACTIVE', gone: null } as api.MonitorState
    mockState([row({ id: 1, state: 'ACTIVE', mode: 'cyclic' })], {}, monitor)
    render(<Recorders spool={spool()} />)
    expect(within(rowOf(1)).queryAllByRole('button')).toHaveLength(0)
  })
  it('the explanation is behind ⓘ', async () => {
    mockState([])
    render(<Recorders spool={spool()} />)
    const note = screen.getByText(/A recorder keeps packets on the server/)
    expect(note).not.toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'About recorders' }))
    expect(note).toBeVisible()
  })
  it('states the hardware stop effect with its scope', () => {
    mockState([])
    render(<Recorders spool={spool()} />)
    const text = screen.getByText(/more than one DP core/).textContent ?? ''
    expect(text).toMatch(/virtio, vmxnet3 or iavf/)
    expect(text).toMatch(/no running capture still receives on/)
    expect(text).toMatch(/stopping a capture \(Stop, or Save of a running recorder\)/)
    expect(text).toMatch(/Removing a capture does not\./)
    expect(text).toMatch(/On af_packet, i40e, ice, ixgbe and mlx5 it does not happen, except when TRex runs with --software and more than one DP core/)
  })
  // The Save tests below: the call is made with no progress yet, progress arrives, then the call settles (p3c-capture-120).
  it('a Save whose last progress message never arrived shows the file from its own result', async () => {
    const r = [row({ id: 1, count: 10 })]
    mockState(r)
    let done!: (v: { file: api.SpoolFile }) => void
    vi.spyOn(api, 'recorderSave').mockReturnValue(new Promise((res) => { done = res }))
    const { rerender } = render(<Recorders spool={spool()} />)
    await userEvent.click(within(rowOf(1)).getByRole('button', { name: 'Save' }))
    mockState(r, { captureSave: { 1: save({ fetched: 5, total: 10 }) } })
    rerender(<Recorders spool={spool()} />)
    await act(async () => { done({ file: file() }) })
    expect(within(rowOf(1)).getByText('Saved as rec-1-x.pcap')).toBeInTheDocument()
    expect(screen.queryByText(/fetched 5 of 10/)).toBeNull()
    expect(within(rowOf(1)).queryByRole('button', { name: 'Save' })).toBeNull()
  })
  it('a failed Save with progress messages shows the error and Resume', async () => {
    const r = [row({ id: 1, count: 10 })]
    mockState(r)
    let fail!: (e: unknown) => void
    vi.spyOn(api, 'recorderSave').mockReturnValue(new Promise((_, rej) => { fail = rej }))
    const { rerender } = render(<Recorders spool={spool()} />)
    await userEvent.click(within(rowOf(1)).getByRole('button', { name: 'Save' }))
    mockState(r, { captureSave: { 1: save({ fetched: 5, total: 10 }) } })
    rerender(<Recorders spool={spool()} />)
    await act(async () => { fail(new api.ApiError(502, 'x', 'TRex went away')) })
    expect(within(rowOf(1)).getByRole('alert')).toHaveTextContent('TRex went away')
    expect(within(rowOf(1)).getByRole('button', { name: 'Resume' })).toBeEnabled()
  })
  it('a failed Stop does not relabel Save as Resume', async () => {
    // A never-saved row whose Stop fails: the error shows, and Save stays Save (only a failed Save call offers Resume).
    mockState([row({ id: 1, state: 'ACTIVE', count: 10 })])
    vi.spyOn(api, 'recorderStop').mockRejectedValue(new api.ApiError(502, 'x', 'stop went wrong'))
    const { rerender } = render(<Recorders spool={spool()} />)
    await userEvent.click(within(rowOf(1)).getByRole('button', { name: 'Stop' }))
    mockState([row({ id: 1, state: 'STOPPED', count: 10 })])
    rerender(<Recorders spool={spool()} />)
    expect(within(rowOf(1)).getByRole('alert')).toHaveTextContent('stop went wrong')
    expect(within(rowOf(1)).getByRole('button', { name: 'Save' })).toBeEnabled()
    expect(within(rowOf(1)).queryByRole('button', { name: 'Resume' })).toBeNull()
  })
  it('a Save result does not carry over to a new connection', async () => {
    const r = [row({ id: 1, count: 10 })]
    mockState(r, { session: session({ connect_seq: 'u:1' }) })
    vi.spyOn(api, 'recorderSave').mockResolvedValue({ file: file() })
    const { rerender } = render(<Recorders spool={spool()} />)
    await userEvent.click(within(rowOf(1)).getByRole('button', { name: 'Save' }))
    expect(await screen.findByText('Saved as rec-1-x.pcap')).toBeInTheDocument()
    mockState([row({ id: 1, count: 3 })], { session: session({ connect_seq: 'u:2' }) })
    rerender(<Recorders spool={spool()} />)
    expect(within(rowOf(1)).getByRole('button', { name: 'Save' })).toBeEnabled()
    expect(screen.queryByText(/Saved as/)).toBeNull()
  })
})
