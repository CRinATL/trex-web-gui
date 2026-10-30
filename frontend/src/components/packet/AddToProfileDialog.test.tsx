import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import { ApiError, SIDECAR_ENTRY_MAX, type LibraryEntry, type PacketDoc, type StreamJson } from '../../api'
import { AddToProfileDialog } from './AddToProfileDialog'

vi.mock('../../api', async (orig) => ({ ...(await orig<typeof import('../../api')>()), listLibrary: vi.fn(), getProfile: vi.fn(), getTemplate: vi.fn(), putStreams: vi.fn(), toStream: vi.fn(), createProfile: vi.fn() }))

const doc: PacketDoc = { fileType: 'trex-packet-editor', version: '1.0.0', metadata: { caption: '' }, fePrarameters: {}, feInstructions: [], packet: [{ id: 'Ether', fields: [] }, { id: 'IP', fields: [] }] }
const entry = (slug: string, name: string, extra: Partial<LibraryEntry> = {}): LibraryEntry => ({
  slug, name, path: `/lib/${slug}.json`, stream_count: 2, created: '', updated: '', source: null, source_missing: false, load_error: null, ...extra,
})
const template: StreamJson = {
  enabled: true, self_start: true, isg: 0, action_count: 0, flags: 0, mode: { type: 'continuous', rate: { type: 'pps', value: 1 } },
  packet: { binary: 'AAAA' }, vm: { instructions: [] }, flow_stats: { enabled: false },
}
const vm = { instructions: [{ type: 'fix_checksum_ipv4', pkt_offset: 14 }] }
const sent = () => vi.mocked(api.putStreams).mock.calls.at(-1)!
const reply = (n: number) => ({ rev: 'r9', stream_count: n, renamed: {}, cleared: [], warnings: [] })

beforeEach(() => {
  vi.mocked(api.listLibrary).mockResolvedValue({ dir: '/lib', load_errors: [], profiles: [entry('a', 'Alpha'), entry('b', 'Beta', { stream_count: 1 }), entry('bad', 'Broken', { load_error: 'sidecar is not JSON' })] })
  vi.mocked(api.getProfile).mockResolvedValue({ entry: entry('b', 'Beta'), rev: 'r1', sidecar: {} as never })
  vi.mocked(api.getTemplate).mockResolvedValue(template)
  vi.mocked(api.toStream).mockResolvedValue({ packet: { binary: 'QUJD', meta: '' }, vm })
  vi.mocked(api.putStreams).mockResolvedValue(reply(2))
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

const renderIt = (props: { onClose?: () => void; d?: PacketDoc; flags?: number | null } = {}) =>
  render(<MemoryRouter><AddToProfileDialog doc={props.d ?? doc} flags={props.flags} onClose={props.onClose ?? vi.fn()} /><Where /></MemoryRouter>)
function Where() { const l = useLocation(); return <span data-testid="where">{l.pathname}{JSON.stringify(l.state)}</span> }

async function pickBeta() {
  const select = await screen.findByRole('combobox', { name: 'Profile' })
  await userEvent.selectOptions(select, 'b')
}

describe('AddToProfileDialog', () => {
  it('adds the packet as a new stream to the chosen profile', async () => {
    renderIt()
    await pickBeta()
    expect(screen.getByRole('option', { name: 'Alpha (2 streams)' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Beta (1 stream)' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /^Broken .*cannot be changed: sidecar is not JSON/ })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(api.putStreams).toHaveBeenCalledTimes(1))
    expect(api.toStream).toHaveBeenCalledWith(doc)
    const [slug, diff] = sent()
    expect(slug).toBe('b')
    expect(diff.rev).toBe('r1')
    // P4a's template stream (continuous, 1 pps) with the packet and the VM replaced
    expect(diff.added).toEqual([{ ...template, packet: { binary: 'QUJD', meta: '' }, vm }])
  })
  it('the new stream carries the editor doc in its sidecar', async () => {
    renderIt()
    await pickBeta()
    await userEvent.click(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(api.putStreams).toHaveBeenCalled())
    expect(sent()[1].added_sidecar).toEqual([{ editor: { doc, binary: 'QUJD', vm } }])
  })
  it('a doc over the entry limit is stored as {} with a note', async () => {
    const big: PacketDoc = { ...doc, packet: [{ id: 'Ether', fields: [{ id: 'payload', value: 'x'.repeat(SIDECAR_ENTRY_MAX) }] }] }
    renderIt({ d: big })
    await pickBeta()
    await userEvent.click(screen.getByRole('button', { name: 'Add' }))
    expect(await screen.findByText(/too large to store with the stream/)).toBeInTheDocument()
    expect(sent()[1].added_sidecar).toEqual([{}])
  })
  it('a preset\'s MAC-mode flags go on the stream', async () => {
    renderIt({ flags: 3 })
    await pickBeta()
    await userEvent.click(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(api.putStreams).toHaveBeenCalled())
    expect(sent()[1].added?.[0].flags).toBe(3)
  })
  it('a stale rev is re-read once and retried', async () => {
    vi.mocked(api.getProfile).mockResolvedValueOnce({ entry: entry('b', 'Beta'), rev: 'old', sidecar: {} as never }).mockResolvedValueOnce({ entry: entry('b', 'Beta'), rev: 'new', sidecar: {} as never })
    vi.mocked(api.putStreams).mockRejectedValueOnce(new ApiError(409, 'stale', 'The profile changed')).mockResolvedValueOnce(reply(2))
    renderIt()
    await pickBeta()
    await userEvent.click(screen.getByRole('button', { name: 'Add' }))
    expect(await screen.findByText(/Added as stream 1 of Beta/)).toBeInTheDocument()     // the reply counts 2 streams; the table shows the new row as #1
    expect(api.putStreams).toHaveBeenCalledTimes(2)
    expect(vi.mocked(api.putStreams).mock.calls.map((c) => c[1].rev)).toEqual(['old', 'new'])
    expect(api.getProfile).toHaveBeenCalledTimes(2)
  })
  it('a second stale reply is shown, and nothing more is sent', async () => {
    vi.mocked(api.putStreams).mockRejectedValue(new ApiError(409, 'stale', 'The profile changed again'))
    renderIt()
    await pickBeta()
    await userEvent.click(screen.getByRole('button', { name: 'Add' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('The profile changed again')
    expect(api.putStreams).toHaveBeenCalledTimes(2)
  })
  it('a 422 at STREAMS_MAX shows on the error line', async () => {
    vi.mocked(api.putStreams).mockRejectedValue(new ApiError(422, 'invalid_stream', 'A profile holds at most 10000 streams'))
    renderIt()
    await pickBeta()
    await userEvent.click(screen.getByRole('button', { name: 'Add' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('A profile holds at most 10000 streams')
    expect(api.putStreams).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Add' })).toBeEnabled()
  })
  it('an empty library names a new profile here, and the packet becomes its first stream (RC1-23)', async () => {
    vi.mocked(api.listLibrary).mockResolvedValue({ dir: '/lib', load_errors: [], profiles: [] })
    vi.mocked(api.createProfile).mockResolvedValue(entry('dns', 'DNS', { stream_count: 0 }))
    vi.mocked(api.putStreams).mockResolvedValue(reply(1))
    renderIt()
    const name = await screen.findByRole('textbox', { name: 'New profile name' })
    await waitFor(() => expect(name).toHaveFocus())
    expect(screen.queryByRole('combobox', { name: 'Profile' })).toBeNull()
    const go = screen.getByRole('button', { name: 'Create and add' })
    expect(go).toBeDisabled()
    expect(go).toHaveAttribute('data-run', 'go')
    await userEvent.type(name, '  DNS {Enter}')
    await waitFor(() => expect(api.putStreams).toHaveBeenCalledTimes(1))
    expect(api.createProfile).toHaveBeenCalledWith('DNS')
    expect(sent()[0]).toBe('dns')
    expect(await screen.findByRole('status')).toHaveTextContent('Added as stream 0 of DNS.')
  })
  it('New profile… in the list creates one; a failed add retries into it without creating a second', async () => {
    vi.mocked(api.createProfile).mockResolvedValue(entry('c', 'Gamma', { stream_count: 0 }))
    vi.mocked(api.putStreams).mockRejectedValueOnce(new ApiError(422, 'invalid_stream', 'bad stream')).mockResolvedValueOnce(reply(1))
    renderIt()
    await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Profile' }), 'New profile…')
    await userEvent.type(screen.getByRole('textbox', { name: 'New profile name' }), 'Gamma')
    await userEvent.click(screen.getByRole('button', { name: 'Create and add' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('bad stream')
    expect(screen.getByRole('combobox', { name: 'Profile' })).toHaveValue('c')   // the new profile is listed and chosen
    await userEvent.click(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(api.putStreams).toHaveBeenCalledTimes(2))
    expect(api.createProfile).toHaveBeenCalledTimes(1)
    expect(sent()[0]).toBe('c')
  })
  it('success links to the profile in Studio', async () => {
    const onClose = vi.fn()
    renderIt({ onClose })
    await pickBeta()
    await userEvent.click(screen.getByRole('button', { name: 'Add' }))
    await userEvent.click(await screen.findByRole('link', { name: 'Open Beta in Studio' }))
    expect(screen.getByTestId('where')).toHaveTextContent('/studio{"studioSelect":"b"}')
    expect(onClose).toHaveBeenCalled()
  })
})
