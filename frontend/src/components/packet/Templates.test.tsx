import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import { ApiError, type PacketDoc, type TemplateList } from '../../api'
import fixture from '../../__fixtures__/packet/templates.json'
import { Templates } from './Templates'

vi.mock('../../api', async (orig) => ({ ...(await orig<typeof import('../../api')>()), listTemplates: vi.fn(), serverTemplate: vi.fn(), userTemplate: vi.fn(), saveUserTemplate: vi.fn(), deleteUserTemplate: vi.fn() }))

const listing = fixture.listing as unknown as TemplateList
const doc = (caption: string): PacketDoc => ({ fileType: 'trex-packet-editor', version: '1.0.0', metadata: { caption }, fePrarameters: {}, feInstructions: [], packet: [{ id: 'Ether', fields: [] }] })

beforeEach(() => { vi.mocked(api.listTemplates).mockResolvedValue(listing) })
afterEach(() => { cleanup(); vi.clearAllMocks() })

const shown = async () => { await screen.findByRole('button', { name: 'TCP-SYN' }) }

describe('Templates', () => {
  it('template_tcp_syn opens TCP-SYN', async () => {
    vi.mocked(api.serverTemplate).mockResolvedValue({ doc: doc('TCP-SYN') })
    const onOpen = vi.fn()
    render(<Templates onOpen={onOpen} onClose={vi.fn()} />)
    await shown()
    await userEvent.click(screen.getByRole('button', { name: 'TCP-SYN' }))
    await waitFor(() => expect(onOpen).toHaveBeenCalledWith(doc('TCP-SYN'), 'TCP-SYN'))
    expect(api.serverTemplate).toHaveBeenCalledWith('TCP-SYN')
  })
  it('tree groups by slash', async () => {
    vi.mocked(api.serverTemplate).mockResolvedValue({ doc: doc('x') })
    const onOpen = vi.fn()
    render(<Templates onOpen={onOpen} onClose={vi.fn()} />)
    await shown()
    const ipv4 = screen.getByText('IPv4/').closest('details')!
    expect(within(ipv4).getAllByRole('button').map((b) => b.textContent)).toEqual(['ICMP', 'TCP', 'UDP'])   // by path: the files' own names are wrong
    expect(screen.getAllByText('ARP/')).toHaveLength(1)
    expect(screen.queryByRole('button', { name: 'ARP-REPLY' })).toBeInTheDocument()
    await userEvent.click(within(ipv4).getByRole('button', { name: 'TCP' }))
    await waitFor(() => expect(onOpen).toHaveBeenCalled())
    expect(api.serverTemplate).toHaveBeenCalledWith('IPv4/TCP')
    expect(onOpen.mock.calls[0][1]).toBe('IPv4/TCP')
    // a template the service cannot build is listed, disabled, with its reason
    expect(screen.getByRole('button', { name: 'IGMP-JOIN' })).toBeDisabled()
    expect(screen.getByText(/two Scapy classes are named IGMP/)).toBeInTheDocument()
  })
  it('save asks before replacing', async () => {
    vi.mocked(api.saveUserTemplate).mockRejectedValueOnce(new ApiError(409, 'exists', 'Template lab/syn exists')).mockResolvedValueOnce({ doc: doc('mine') })
    render(<Templates doc={doc('mine')} onOpen={vi.fn()} onClose={vi.fn()} />)
    await shown()
    await userEvent.type(screen.getByLabelText('Save the packet as a template'), 'lab/syn')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText('Replace lab/syn?')).toBeInTheDocument()
    expect(api.saveUserTemplate).toHaveBeenLastCalledWith('lab/syn', doc('mine'), false)
    await userEvent.click(screen.getByRole('button', { name: 'Replace' }))
    await waitFor(() => expect(api.saveUserTemplate).toHaveBeenLastCalledWith('lab/syn', doc('mine'), true))
    expect(await screen.findByText('Saved lab/syn')).toBeInTheDocument()
    expect(screen.queryByText('Replace lab/syn?')).toBeNull()
  })
  it('nested user names show as a tree', async () => {
    vi.mocked(api.listTemplates).mockResolvedValue({ ...listing, user: ['lab/a/one', 'lab/two', 'top'] })
    vi.mocked(api.userTemplate).mockResolvedValue({ doc: doc('one') })
    vi.mocked(api.deleteUserTemplate).mockResolvedValue({})
    const onOpen = vi.fn()
    render(<Templates onOpen={onOpen} onClose={vi.fn()} />)
    await shown()
    const lab = screen.getByText('lab/').closest('details')!
    expect(within(lab).getByText('a/')).toBeInTheDocument()
    expect(within(lab).getByRole('button', { name: 'two' })).toBeInTheDocument()
    await userEvent.click(within(lab).getByRole('button', { name: 'one' }))
    await waitFor(() => expect(onOpen).toHaveBeenCalledWith(doc('one'), 'lab/a/one'))
    expect(api.userTemplate).toHaveBeenCalledWith('lab/a/one')
    // delete asks first
    await userEvent.click(screen.getByRole('button', { name: 'Delete top' }))
    expect(api.deleteUserTemplate).not.toHaveBeenCalled()
    await userEvent.click(within(screen.getByText(/^Delete top\?/).closest('p')!).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(api.deleteUserTemplate).toHaveBeenCalledWith('top'))
  })
})
