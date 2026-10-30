import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import { ApiError, type PacketDoc, type PcapPage } from '../../api'
import { PcapPicker } from './PcapPicker'

vi.mock('../../api', async (orig) => ({ ...(await orig<typeof import('../../api')>()), pcapList: vi.fn(), pcapPacket: vi.fn(), uploadFile: vi.fn() }))

const doc: PacketDoc = { fileType: 'trex-packet-editor', version: '1.0.0', metadata: { caption: '' }, fePrarameters: {}, feInstructions: [], packet: [{ id: 'Ether', fields: [] }] }
const page = (offset: number, n: number, total: number, warnings: string[] = []): PcapPage => ({
  total, warnings, rows: Array.from({ length: n }, (_, i) => ({ index: offset + i, length: 60 + i, summary: `Ether / IP ${offset + i}` })),
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

async function list(path = '/caps/a.pcap') {
  await userEvent.type(screen.getByLabelText('Capture path'), path)
  await userEvent.click(screen.getByRole('button', { name: 'List packets' }))
}

describe('PcapPicker', () => {
  it('pages of 100 with a range line', async () => {
    vi.mocked(api.pcapList).mockImplementation(async (_s, offset) => page(offset, offset === 200 ? 50 : 100, 250))
    render(<PcapPicker onPick={vi.fn()} onClose={vi.fn()} />)
    await list()
    expect(await screen.findByText(/Packets 1–100 of 250/)).toBeInTheDocument()
    expect(api.pcapList).toHaveBeenLastCalledWith({ path: '/caps/a.pcap' }, 0)
    expect(screen.getAllByRole('row')).toHaveLength(101)             // 100 rows and the header
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(await screen.findByText(/Packets 101–200 of 250/)).toBeInTheDocument()
    expect(api.pcapList).toHaveBeenLastCalledWith({ path: '/caps/a.pcap' }, 100)
    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(await screen.findByText(/Packets 201–250 of 250/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Previous' }))
    expect(await screen.findByText(/Packets 101–200 of 250/)).toBeInTheDocument()
  })
  it('picking opens the packet', async () => {
    vi.mocked(api.pcapList).mockResolvedValue(page(0, 3, 3))
    vi.mocked(api.pcapPacket).mockResolvedValue({ binary: 'AA==', doc, warnings: ['kept as a literal'] })
    const onPick = vi.fn()
    render(<PcapPicker onPick={onPick} onClose={vi.fn()} />)
    await list()
    await userEvent.click(await screen.findByRole('button', { name: 'Open packet 2' }))
    await waitFor(() => expect(onPick).toHaveBeenCalledWith(doc, ['kept as a literal']))
    expect(api.pcapPacket).toHaveBeenCalledWith({ path: '/caps/a.pcap' }, 1)
  })
  it('a 413 names the size cap setting', async () => {
    vi.mocked(api.pcapList).mockRejectedValue(new ApiError(413, 'too_large', '/caps/a.pcap is 99999999 bytes'))
    render(<PcapPicker onPick={vi.fn()} onClose={vi.fn()} />)
    await list()
    expect(await screen.findByRole('alert')).toHaveTextContent(/99999999 bytes.*pcap_size_cap_mib setting/)
  })
  it("the page's truncation warnings are shown", async () => {
    vi.mocked(api.pcapList).mockResolvedValue(page(0, 2, 2, ['the capture ends in the middle of record 3', 'record 1 was cut at 65535 bytes']))
    render(<PcapPicker onPick={vi.fn()} onClose={vi.fn()} />)
    await list()
    const w = await screen.findByRole('status', { name: 'Capture warnings' })
    expect(within(w).getAllByRole('listitem').map((l) => l.textContent)).toEqual(['the capture ends in the middle of record 3', 'record 1 was cut at 65535 bytes'])
    // above the rows
    expect(w.compareDocumentPosition(screen.getByRole('table')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
  it('a file from this computer is uploaded once and paged by its upload id', async () => {
    vi.mocked(api.uploadFile).mockResolvedValue({ upload_id: 'u1', name: 'b.pcap', size: 5 })
    vi.mocked(api.pcapList).mockImplementation(async (_s, offset) => page(offset, offset === 0 ? 100 : 50, 150))
    render(<PcapPicker onPick={vi.fn()} onClose={vi.fn()} />)
    await userEvent.click(screen.getByRole('radio', { name: 'From this computer' }))
    await userEvent.upload(screen.getByLabelText('Capture file'), new File(['x'], 'b.pcap'))
    await userEvent.click(screen.getByRole('button', { name: 'List packets' }))
    await screen.findByText(/Packets 1–100 of 150/)
    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    await screen.findByText(/Packets 101–150 of 150/)
    expect(api.uploadFile).toHaveBeenCalledTimes(1)
    expect(api.pcapList).toHaveBeenLastCalledWith({ upload_id: 'u1' }, 100)
  })
})
