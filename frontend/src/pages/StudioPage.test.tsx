import type { ReactElement } from 'react'
import { act, cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useNavigate } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import type { BuildResult, Definitions, LibraryEntry, LibraryList, PacketDoc, StreamPage } from '../api'
import buildFixture from '../__fixtures__/packet/buildUdp.json'
import defsFixture from '../__fixtures__/packet/definitions.json'
import library from '../__fixtures__/studioLibrary.json'
import streams from '../__fixtures__/studioStreams.json'
import { StudioPage } from './StudioPage'

afterEach(() => { cleanup(); vi.restoreAllMocks() })
const fixture = library.udp_for_benchmarks as unknown as LibraryList
const empty: LibraryList = { profiles: [], load_errors: [], dir: '/lib' }

// Two profiles; the first open on the Packet tab of stream 0 with its TTL edited in the embedded editor.
const first = fixture.profiles[0] as LibraryEntry
const second: LibraryEntry = { ...first, slug: 'other', name: 'other', path: '/lib/other.json' }
async function dirtyProfile(ui: (active: boolean) => ReactElement, beforeEdit?: (user: ReturnType<typeof userEvent.setup>) => Promise<void>) {
  vi.spyOn(api, 'listLibrary').mockResolvedValue({ ...fixture, profiles: [first, second] })
  vi.spyOn(api, 'getStreams').mockResolvedValue(streams.udp_for_benchmarks as unknown as StreamPage)
  const udp = buildFixture.udp as unknown as BuildResult
  vi.spyOn(api, 'packetDefinitions').mockResolvedValue((defsFixture as unknown as { definitions: Definitions }).definitions)
  vi.spyOn(api, 'buildPacket').mockImplementation(async (d) => ({ ...udp, data: d.packet.map((l, i) => ({ ...(udp.data.find((x) => x.id === l.id) ?? udp.data[3]), id: l.id, offset: udp.data[Math.min(i, 3)].offset })) }))
  vi.spyOn(api, 'payloadClasses').mockResolvedValue({ classes: [] })
  const doc: PacketDoc = { fileType: 'trex-packet-editor', version: '1.0.0', metadata: { caption: '' }, fePrarameters: {}, feInstructions: [],
    packet: [{ id: 'Ether', fields: [] }, { id: 'IP', fields: [] }, { id: 'UDP', fields: [] }, { id: 'Raw', fields: [] }] }
  vi.spyOn(api, 'docFromBinary').mockResolvedValue({ doc, warnings: [] })
  const user = userEvent.setup()
  const view = render(ui(true))
  await user.click(await screen.findByRole('option', { name: /udp_for_benchmarks/ }))
  await beforeEdit?.(user)
  await user.click(await screen.findByRole('cell', { name: '0' }))
  await user.click(within(screen.getByRole('tablist', { name: 'Stream' })).getByRole('tab', { name: 'Packet' }))
  await user.click(screen.getByRole('button', { name: 'Edit packet' }))
  await vi.waitFor(() => expect(document.querySelector('.layer-list')).not.toBeNull())
  await user.click(within(document.querySelector('.layer-list') as HTMLElement).getByRole('button', { name: 'IP' }))
  const ttl = await screen.findByRole('textbox', { name: 'TTL' })
  await user.clear(ttl)
  await user.type(ttl, '9{Enter}')
  return { user, view }
}
const asked = () => screen.queryByRole('dialog', { name: 'Discard the packet edits?' })

describe('StudioPage', () => {
  it('re-reads the library when the page becomes active', async () => {
    const list = vi.spyOn(api, 'listLibrary').mockResolvedValue(fixture)
    const { rerender } = render(<StudioPage active={false} />)
    expect(list).not.toHaveBeenCalled()
    rerender(<StudioPage active />)
    expect(await screen.findByRole('option', { name: /udp_for_benchmarks/ })).toBeInTheDocument()
    expect(list).toHaveBeenCalledTimes(1)
    rerender(<StudioPage active={false} />)
    rerender(<StudioPage active />)
    await vi.waitFor(() => expect(list).toHaveBeenCalledTimes(2))
  })

  it('a studioSelect request from Control selects that entry', async () => {
    vi.spyOn(api, 'listLibrary').mockResolvedValue(fixture)
    vi.spyOn(api, 'getStreams').mockResolvedValue(streams.udp_for_benchmarks as unknown as StreamPage)
    render(<MemoryRouter initialEntries={[{ pathname: '/studio', state: { studioSelect: 'udp-for-benchmarks' } }]}><StudioPage active /></MemoryRouter>)
    expect(await screen.findByRole('option', { name: /udp_for_benchmarks/, selected: true })).toBeInTheDocument()
  })

  it('an empty library says how to start', async () => {
    vi.spyOn(api, 'listLibrary').mockResolvedValue(empty)
    render(<StudioPage active />)
    expect(await screen.findByText('No profiles yet. New… or Import… to add one.')).toBeInTheDocument()
  })

  it('a read failure shows its message', async () => {
    vi.spyOn(api, 'listLibrary').mockRejectedValue(new api.ApiError(500, 'storage_error', 'cannot read the library'))
    render(<StudioPage active />)
    expect(await screen.findByRole('alert')).toHaveTextContent('cannot read the library')
  })

  it('a write re-reads the list and selects the new entry', async () => {
    const created = { ...(fixture.profiles[0] as LibraryEntry), slug: 'fresh', name: 'fresh', path: '/lib/fresh.json' }
    const list = vi.spyOn(api, 'listLibrary').mockResolvedValueOnce(empty).mockResolvedValue({ ...empty, profiles: [created] })
    vi.spyOn(api, 'createProfile').mockResolvedValue(created)
    render(<StudioPage active />)
    await screen.findByText(/No profiles yet/)
    const { default: userEvent } = await import('@testing-library/user-event')
    await userEvent.click(screen.getByRole('button', { name: 'New…' }))
    await userEvent.type(screen.getByRole('textbox', { name: 'Name' }), 'fresh')
    await userEvent.click(screen.getByRole('button', { name: 'Create' }))
    expect(await screen.findByRole('option', { name: /fresh/, selected: true })).toBeInTheDocument()
    expect(list).toHaveBeenCalledTimes(2)
  })

  it('while the page is hidden, Escape neither closes a Studio dialog nor releases the table lock', async () => {
    vi.spyOn(api, 'listLibrary').mockResolvedValue(fixture)
    vi.spyOn(api, 'getStreams').mockResolvedValue(streams.udp_for_benchmarks as unknown as StreamPage)
    vi.spyOn(api, 'importProfile').mockImplementation(() => new Promise(() => undefined))   // the append stays out
    const user = userEvent.setup()
    const { rerender } = render(<StudioPage active />)
    await user.click(await screen.findByRole('option', { name: /udp_for_benchmarks/ }))
    await user.click(await screen.findByRole('button', { name: 'Import stream from pcap…' }))
    await user.type(screen.getByRole('textbox', { name: 'Path' }), '/srv/t.pcap')
    await user.click(screen.getByRole('button', { name: 'Import' }))
    expect(screen.getByRole('checkbox', { name: 'Enabled #0' })).toBeDisabled()
    rerender(<StudioPage active={false} />)
    await user.keyboard('{Escape}')
    expect(screen.getByRole('dialog', { name: 'Import streams from pcap' })).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Enabled #0' })).toBeDisabled()
    rerender(<StudioPage active />)
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('while the page is hidden, Escape leaves the library import dialog and its Browse open', async () => {
    vi.spyOn(api, 'listLibrary').mockResolvedValue(fixture)
    vi.spyOn(api, 'fsList').mockResolvedValue({ path: '/srv', parent: '/', entries: [], truncated: false })
    const user = userEvent.setup()
    const { rerender } = render(<StudioPage active />)
    await user.click(await screen.findByRole('button', { name: 'Import…' }))
    await user.click(screen.getByRole('button', { name: 'Browse…' }))
    expect(await screen.findByRole('dialog', { name: 'Browse for a file to import' })).toBeInTheDocument()
    rerender(<StudioPage active={false} />)
    await user.keyboard('{Escape}')
    expect(screen.getByRole('dialog', { name: 'Browse for a file to import' })).toBeInTheDocument()
    expect(screen.getByRole('dialog', { name: 'Import a profile' })).toBeInTheDocument()
  })

  it('selecting another profile with unsaved packet edits asks first', async () => {
    const confirm = vi.spyOn(window, 'confirm')
    const { user } = await dirtyProfile((a) => <StudioPage active={a} />)
    await user.click(screen.getByRole('option', { name: /other/ }))
    expect(await screen.findByRole('dialog', { name: 'Discard the packet edits?' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Keep editing' }))
    expect(screen.queryByRole('dialog', { name: 'Discard the packet edits?' })).not.toBeInTheDocument()
    expect(screen.getByRole('option', { name: /udp_for_benchmarks/, selected: true })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reset TTL to default' })).toBeInTheDocument()

    await user.click(screen.getByRole('option', { name: /other/ }))
    await user.click(await screen.findByRole('button', { name: 'Discard' }))
    expect(screen.getByRole('option', { name: /other/, selected: true })).toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: 'Field engine' })).not.toBeInTheDocument()
    // the next switch asks nothing: the edits are gone
    await user.click(screen.getByRole('option', { name: /udp_for_benchmarks/ }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(confirm).not.toHaveBeenCalled()
  })

  it('a profile re-selected by a library write (New…) asks while the editor has unsaved edits', async () => {
    const { user } = await dirtyProfile((a) => <StudioPage active={a} />)
    const created = { ...first, slug: 'fresh', name: 'fresh', path: '/lib/fresh.json' }
    vi.spyOn(api, 'listLibrary').mockResolvedValue({ ...fixture, profiles: [first, second, created] })
    vi.spyOn(api, 'createProfile').mockResolvedValue(created)
    await user.click(screen.getByRole('button', { name: 'New…' }))
    await user.type(screen.getByRole('textbox', { name: 'Name' }), 'fresh')
    await user.click(screen.getByRole('button', { name: 'Create' }))
    expect(await screen.findByRole('dialog', { name: 'Discard the packet edits?' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Keep editing' }))
    expect(screen.getByRole('option', { name: /udp_for_benchmarks/, selected: true })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reset TTL to default' })).toBeInTheDocument()
  })

  it('Rename… and Delete are off while the open profile has unsaved edits', async () => {
    const { user } = await dirtyProfile((a) => <StudioPage active={a} />)
    const lib = within(screen.getByRole('toolbar', { name: 'udp_for_benchmarks actions' }))
    for (const name of ['Rename…', 'Delete']) {
      expect(lib.getByRole('button', { name })).toBeDisabled()
      expect(lib.getByRole('button', { name })).toHaveAttribute('title', expect.stringContaining('unsaved edits'))
    }
    await user.click(screen.getByRole('button', { name: 'Cancel' }))     // the editor's Cancel asks, then Discard
    await user.click(await screen.findByRole('button', { name: 'Discard' }))
    expect(lib.getByRole('button', { name: 'Rename…' })).toBeEnabled()
  })

  it('a Rename form opened while clean cannot be submitted once the packet is edited', async () => {
    const rename = vi.spyOn(api, 'renameProfile').mockResolvedValue(first)
    await dirtyProfile((a) => <StudioPage active={a} />, async (user) => {
      await user.click(within(screen.getByRole('toolbar', { name: 'udp_for_benchmarks actions' })).getByRole('button', { name: 'Rename…' }))
      const name = within(screen.getByRole('group', { name: 'Rename profile' })).getByRole('textbox', { name: 'Name' })
      await user.clear(name); await user.type(name, 'renamed')
    })     // the editor went dirty while the form stayed open
    const form = within(screen.getByRole('group', { name: 'Rename profile' }))
    expect(form.getByRole('button', { name: 'Rename' })).toBeDisabled()
    expect(form.getByRole('button', { name: 'Rename' })).toHaveAttribute('title', expect.stringContaining('unsaved edits'))
    await userEvent.setup().type(form.getByRole('textbox', { name: 'Name' }), '{Enter}')
    expect(rename).not.toHaveBeenCalled()
  })

  it('a studioSelect navigation made while Studio is hidden asks when Studio is next shown', async () => {
    let go: (slug: string) => void = () => undefined
    function Nav() { const navigate = useNavigate(); go = (slug) => navigate('/studio', { state: { studioSelect: slug } }); return null }
    const ui = (a: boolean) => <MemoryRouter initialEntries={['/studio']}><Nav /><StudioPage active={a} /></MemoryRouter>
    const { user, view } = await dirtyProfile(ui)
    view.rerender(ui(false))
    act(() => go('other'))
    expect(asked()).not.toBeInTheDocument()                       // hidden: nothing is read, nothing is switched
    expect(screen.getByRole('option', { name: /udp_for_benchmarks/, selected: true })).toBeInTheDocument()
    view.rerender(ui(true))
    expect(await screen.findByRole('dialog', { name: 'Discard the packet edits?' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /udp_for_benchmarks/, selected: true })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Discard' }))
    expect(screen.getByRole('option', { name: /other/, selected: true })).toBeInTheDocument()
  })
})
