import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import type { BuildResult, Definitions, PacketDoc } from '../api'
import buildFixture from '../__fixtures__/packet/buildUdp.json'
import defsFixture from '../__fixtures__/packet/definitions.json'
import { writeTrp } from '../components/packet/trpFile'
import * as download from '../download'
import { PacketPage } from './PacketPage'

vi.mock('../api', async (orig) => ({
  ...(await orig<typeof import('../api')>()),
  buildPacket: vi.fn(), packetDefinitions: vi.fn(), payloadClasses: vi.fn(), toStream: vi.fn(), pcapList: vi.fn(), pcapPacket: vi.fn(), listLibrary: vi.fn(), listTemplates: vi.fn(), serverTemplate: vi.fn(), listPresets: vi.fn(), fsList: vi.fn(),
}))

const defs = (defsFixture as unknown as { definitions: Definitions }).definitions
const udp = buildFixture.udp as unknown as BuildResult
const udpDoc = (caption = ''): PacketDoc => ({
  fileType: 'trex-packet-editor', version: '1.0.0', metadata: { caption }, fePrarameters: { cache_size: '1000' }, feInstructions: [],
  packet: [{ id: 'Ether', fields: [] }, { id: 'IP', fields: [] }, { id: 'UDP', fields: [] }, { id: 'Raw', fields: [] }],
})
const built = (d: PacketDoc): BuildResult => ({
  ...udp, data: d.packet.map((l, i) => ({ ...(udp.data.find((x) => x.id === l.id) ?? { ...udp.data[3], id: l.id, real_id: l.id }), offset: udp.data[Math.min(i, 3)].offset })),
})
const lastBuilt = () => vi.mocked(api.buildPacket).mock.calls.at(-1)![0]

beforeEach(() => {
  vi.mocked(api.packetDefinitions).mockResolvedValue(defs)
  vi.mocked(api.buildPacket).mockImplementation(async (d) => built(d))
  vi.mocked(api.payloadClasses).mockResolvedValue({ classes: [] })
  vi.mocked(api.listPresets).mockResolvedValue({ presets: [] })
  vi.spyOn(window, 'confirm')   // never called: the page asks in its own dialog
})
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.restoreAllMocks() })

const renderPage = (active = true) => render(<MemoryRouter><PacketPage active={active} /></MemoryRouter>)
const trpFile = (doc: PacketDoc, name = 'lab.trp') => new File([writeTrp(doc)], name, { type: 'application/json' })
const title = () => screen.getByRole('heading', { level: 1 })
const menu = async (item: string | RegExp) => {
  await userEvent.click(screen.getByRole('button', { name: 'File ▾' }))
  await userEvent.click(screen.getByRole('menuitem', { name: item }))
}
const list = () => document.querySelector('.layer-list') as HTMLElement
async function openTrp(doc = udpDoc(), name = 'lab.trp') {
  await userEvent.upload(screen.getByLabelText('Packet file'), trpFile(doc, name))
  await waitFor(() => expect(list()).not.toBeNull())
  await waitFor(() => expect(title()).toHaveTextContent(name.replace('.trp', '')))
}
async function editTtl(value: string) {
  await userEvent.click(within(list()).getByRole('button', { name: 'IP' }))
  const box = await screen.findByRole('textbox', { name: 'TTL' })
  await userEvent.clear(box)
  await userEvent.type(box, `${value}{Enter}`)
}

describe('PacketPage', () => {
  it('Open reads a .trp file and shows its name in the title', async () => {
    renderPage()
    expect(title()).toHaveTextContent('Packet editor — Untitled')
    await openTrp()
    expect(title()).toHaveTextContent('Packet editor — lab')
    expect(title()).not.toHaveTextContent('•')
    await waitFor(() => expect(lastBuilt().packet.map((l) => l.id)).toEqual(['Ether', 'IP', 'UDP', 'Raw']))
  })
  it('a file that is not a packet document is refused, and the packet stays', async () => {
    renderPage()
    await userEvent.upload(screen.getByLabelText('Packet file'), new File(['{"a":1}'], 'bad.trp'))
    expect(await screen.findByRole('alert')).toHaveTextContent(/bad\.trp: not a packet editor file/)
    expect(title()).toHaveTextContent('Untitled')
  })
  it('the Open note says a .trp may evaluate expressions', async () => {
    renderPage()
    await userEvent.click(screen.getByRole('button', { name: 'File ▾' }))
    expect(screen.getByRole('menuitem', { name: 'Open .trp…' })).toHaveAccessibleDescription(/may contain expressions.*evaluated on the backend host/)
  })
  it("the Save note says it downloads, and each note describes only its own item", async () => {
    renderPage()
    await userEvent.click(screen.getByRole('button', { name: 'File ▾' }))
    expect(screen.getByRole('menuitem', { name: 'Save' })).toHaveAccessibleDescription('Downloads a .trp file.')
    expect(screen.getByRole('menuitem', { name: 'Open .trp…' })).not.toHaveAccessibleDescription(/Downloads/)
  })
  const discardDialog = () => screen.findByRole('dialog', { name: 'Discard changes?' })
  it('New clears the document and its history', async () => {
    renderPage()
    await openTrp()
    await editTtl('9')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Undo' })).toBeEnabled())
    expect(title()).toHaveTextContent('•')
    await menu('New')
    const ask = await discardDialog()                              // the edit was unsaved
    expect(title()).toHaveTextContent('lab')                       // nothing has changed yet
    await userEvent.click(within(ask).getByRole('button', { name: 'Discard' }))
    await waitFor(() => expect(lastBuilt().packet.map((l) => l.id)).toEqual(['Ether']))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.getByRole('button', { name: 'Undo' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Redo' })).toBeDisabled()
    expect(title()).toHaveTextContent('Packet editor — Untitled')
    expect(title()).not.toHaveTextContent('•')
    expect(window.confirm).not.toHaveBeenCalled()
  })
  it('New on a clean document asks nothing', async () => {
    renderPage()
    await openTrp()
    await menu('New')
    await waitFor(() => expect(title()).toHaveTextContent('Untitled'))
    expect(screen.queryByRole('dialog')).toBeNull()
  })
  it('Cancel keeps the document and its name', async () => {
    renderPage()
    await openTrp()
    await editTtl('9')
    await waitFor(() => expect(title()).toHaveTextContent('•'))
    await menu('New')
    await userEvent.click(within(await discardDialog()).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(title()).toHaveTextContent('Packet editor — lab •')
    expect(lastBuilt().packet[1].fields).toEqual([{ id: 'ttl', value: 9 }])
    expect(window.confirm).not.toHaveBeenCalled()
  })
  it('Escape cancels the discard question and leaves the document', async () => {
    renderPage()
    await openTrp()
    await editTtl('9')
    await waitFor(() => expect(title()).toHaveTextContent('•'))
    await menu('New')
    await discardDialog()
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(title()).toHaveTextContent('Packet editor — lab •')
  })
  it('Open .trp over unsaved changes asks first, and Escape with a template open closes only the question', async () => {
    vi.mocked(api.listTemplates).mockResolvedValue({ server: [{ id: 'TCP-SYN', name: 'TCP-SYN', buildable: true, reason: null }], user: [], dir: '/t' })
    vi.mocked(api.serverTemplate).mockResolvedValue({ doc: udpDoc('syn') })
    renderPage()
    await openTrp()
    await editTtl('9')
    await waitFor(() => expect(title()).toHaveTextContent('•'))
    await userEvent.upload(screen.getByLabelText('Packet file'), trpFile(udpDoc(), 'other.trp'))
    await userEvent.click(within(await discardDialog()).getByRole('button', { name: 'Cancel' }))
    expect(title()).toHaveTextContent('Packet editor — lab •')
    await userEvent.click(screen.getByRole('button', { name: 'Templates…' }))
    await userEvent.click(await screen.findByRole('button', { name: 'TCP-SYN' }))
    await discardDialog()
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog', { name: 'Discard changes?' })).toBeNull()
    expect(screen.getByRole('dialog', { name: 'Templates' })).toBeInTheDocument()   // the template dialog is still there
    await userEvent.click(screen.getByRole('button', { name: 'TCP-SYN' }))
    await userEvent.click(within(await discardDialog()).getByRole('button', { name: 'Discard' }))
    await waitFor(() => expect(title()).toHaveTextContent('Packet editor — TCP-SYN'))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(window.confirm).not.toHaveBeenCalled()
  })
  it('Save downloads the .trp', async () => {
    const dl = vi.spyOn(download, 'downloadBlob').mockImplementation(() => undefined)
    renderPage()
    await openTrp()
    await editTtl('9')
    await waitFor(() => expect(title()).toHaveTextContent('•'))
    await menu('Save')
    expect(dl).toHaveBeenCalledTimes(1)
    const [name, blob] = dl.mock.calls[0]
    expect(name).toBe('lab.trp')
    const written = JSON.parse(await blob.text()) as PacketDoc & { feParameters?: unknown }
    expect(written.packet[1].fields).toEqual([{ id: 'ttl', value: 9 }])
    expect(written.fePrarameters).toEqual({ cache_size: '1000' })
    expect('feParameters' in written).toBe(false)
    expect(title()).not.toHaveTextContent('•')                      // saved
  })
  it('Ctrl+S in the editor saves', async () => {
    const dl = vi.spyOn(download, 'downloadBlob').mockImplementation(() => undefined)
    renderPage()
    await openTrp()
    await userEvent.click(within(list()).getByRole('button', { name: 'IP' }))
    await userEvent.keyboard('{Control>}s{/Control}')
    expect(dl).toHaveBeenCalledTimes(1)
  })
  it('Save as pcap downloads one packet', async () => {
    const dl = vi.spyOn(download, 'downloadBlob').mockImplementation(() => undefined)
    const bytes = Uint8Array.from({ length: 60 }, (_, i) => i)
    vi.mocked(api.toStream).mockResolvedValue({ packet: { binary: btoa(String.fromCharCode(...bytes)), meta: '' }, vm: { instructions: [] } })
    renderPage()
    await openTrp()
    await menu('Save as pcap')
    await waitFor(() => expect(dl).toHaveBeenCalledTimes(1))
    const [name, blob] = dl.mock.calls[0]
    expect(name).toBe('lab.pcap')
    const raw = new Uint8Array(await blob.arrayBuffer())
    expect(raw.length).toBe(24 + 16 + 60)                           // the global header, one record header, the frame
    expect(new DataView(raw.buffer).getUint32(24 + 8, true)).toBe(60)
    expect(Array.from(raw.slice(40))).toEqual(Array.from(bytes))
    expect(vi.mocked(api.toStream).mock.calls[0][0].packet.map((l) => l.id)).toEqual(['Ether', 'IP', 'UDP', 'Raw'])
  })
  it('Save as pcap shows a refusal', async () => {
    vi.spyOn(download, 'downloadBlob').mockImplementation(() => undefined)
    vi.mocked(api.toStream).mockRejectedValue(new api.ApiError(422, 'invalid_stream', 'the packet is 9300 bytes; the most is 9238'))
    renderPage()
    await menu('Save as pcap')
    expect(await screen.findByRole('alert')).toHaveTextContent('9300 bytes')
  })
  it('the page keeps its document across navigation', async () => {
    const { rerender } = renderPage()
    await openTrp()
    await editTtl('9')
    await waitFor(() => expect(title()).toHaveTextContent('•'))
    rerender(<MemoryRouter><PacketPage active={false} /></MemoryRouter>)     // App.tsx hides the page, it does not unmount it
    rerender(<MemoryRouter><PacketPage active /></MemoryRouter>)
    expect(title()).toHaveTextContent('Packet editor — lab •')
    expect(screen.getByRole('button', { name: 'Undo' })).toBeEnabled()
    expect(lastBuilt().packet[1].fields).toEqual([{ id: 'ttl', value: 9 }])
  })
  it('Add to profile opens the dialog with the current doc', async () => {
    vi.mocked(api.listLibrary).mockResolvedValue({ dir: '/lib', load_errors: [], profiles: [] })
    renderPage()
    await openTrp()
    await editTtl('9')
    await menu('Add to profile as a new stream…')
    const dialog = await screen.findByRole('dialog', { name: 'Add to profile' })
    expect(dialog).toBeInTheDocument()
    // the dialog adds what the editor holds now
    expect(lastBuilt().packet[1].fields).toEqual([{ id: 'ttl', value: 9 }])
  })
  it('a pcap packet that arrives after the document changed is dropped', async () => {
    vi.mocked(api.pcapList).mockResolvedValue({ total: 1, warnings: [], rows: [{ index: 0, length: 60, summary: 'Ether' }] })
    let arrive: (v: { binary: string; doc: PacketDoc; warnings: string[] }) => void = () => undefined
    vi.mocked(api.pcapPacket).mockImplementation(() => new Promise((r) => { arrive = r }))
    renderPage()
    await openTrp()
    await menu('Open pcap…')
    await userEvent.type(screen.getByLabelText('Capture path'), '/caps/a.pcap')
    await userEvent.click(screen.getByRole('button', { name: 'List packets' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Open packet 1' }))
    await editTtl('9')                                                // the document changes while the packet is on its way
    arrive({ binary: 'AA==', doc: udpDoc('from-pcap'), warnings: [] })
    expect(await screen.findByText(/dropped/)).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(title()).toHaveTextContent('lab')
    expect(lastBuilt().packet[1].fields).toEqual([{ id: 'ttl', value: 9 }])
  })
  it("an opened pcap packet's warnings are shown with it", async () => {
    vi.mocked(api.pcapList).mockResolvedValue({ total: 1, warnings: [], rows: [{ index: 0, length: 60, summary: 'Ether' }] })
    vi.mocked(api.pcapPacket).mockResolvedValue({ binary: 'AA==', doc: udpDoc(), warnings: ['record 1 was cut at 65535 bytes'] })
    renderPage()
    await menu('Open pcap…')
    await userEvent.type(screen.getByLabelText('Capture path'), '/caps/a.pcap')
    await userEvent.click(screen.getByRole('button', { name: 'List packets' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Open packet 1' }))
    expect(await screen.findByText('record 1 was cut at 65535 bytes')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).toBeNull()
    await waitFor(() => expect(lastBuilt().packet.map((l) => l.id)).toEqual(['Ether', 'IP', 'UDP', 'Raw']))
  })
  it('Ctrl+S in a dialog neither saves nor reaches the browser', async () => {
    const dl = vi.spyOn(download, 'downloadBlob').mockImplementation(() => undefined)
    renderPage()
    await userEvent.click(screen.getByRole('button', { name: 'Presets…' }))
    const dialog = await screen.findByRole('dialog', { name: 'Presets' })
    const key = new KeyboardEvent('keydown', { key: 's', ctrlKey: true, bubbles: true, cancelable: true })
    within(dialog).getByRole('button', { name: 'Close' }).dispatchEvent(key)
    expect(key.defaultPrevented).toBe(true)
    expect(dl).not.toHaveBeenCalled()
  })
  it('Escape closes each page dialog', async () => {
    vi.mocked(api.listTemplates).mockResolvedValue({ server: [], user: [], dir: '/t' })
    vi.mocked(api.listPresets).mockResolvedValue({ presets: [] })
    vi.mocked(api.listLibrary).mockResolvedValue({ dir: '/lib', load_errors: [], profiles: [] })
    renderPage()
    const cases: [string, () => Promise<void>][] = [
      ['Templates', () => userEvent.click(screen.getByRole('button', { name: 'Templates…' }))],
      ['Presets', () => userEvent.click(screen.getByRole('button', { name: 'Presets…' }))],
      ['Open pcap', () => menu('Open pcap…')],
      ['Add to profile', () => menu('Add to profile as a new stream…')],
    ]
    for (const [name, open] of cases) {
      await open()
      const dialog = await screen.findByRole('dialog', { name })
      expect(dialog.contains(document.activeElement)).toBe(true)
      await userEvent.keyboard('{Escape}')
      expect(screen.queryByRole('dialog')).toBeNull()
    }
  })
  it("Escape in the picker's Browse dialog closes Browse and leaves the picker open", async () => {
    vi.mocked(api.fsList).mockResolvedValue({ path: '/caps', parent: '/', entries: [], truncated: false })
    renderPage()
    await menu('Open pcap…')
    await userEvent.click(screen.getByRole('button', { name: 'Browse…' }))
    await screen.findByRole('dialog', { name: 'Browse for a capture' })
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog', { name: 'Browse for a capture' })).toBeNull()
    expect(screen.getByRole('dialog', { name: 'Open pcap' })).toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})
