import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import type { DiffReply, Inspected, LibraryEntry } from '../../api'
import library from '../../__fixtures__/studioLibrary.json'
import { ImportDialog } from './ImportDialog'
import { PCAP_DEFAULTS } from './pcapOptions'

afterEach(() => { cleanup(); vi.restoreAllMocks() })
const entry = library.udp_for_benchmarks.profiles[0] as unknown as LibraryEntry
const UPLOAD_ID = 'ab'.repeat(16)

function show(appendTo?: { slug: string; send: (build: (rev: string) => api.ImportBody) => Promise<DiffReply> }) {
  const onDone = vi.fn()
  const onClose = vi.fn()
  render(<ImportDialog appendTo={appendTo} onDone={onDone} onClose={onClose} />)
  // applyAccept off: a user can pick any file through the browser's "All files" filter
  return { onDone, onClose, user: userEvent.setup({ applyAccept: false }) }
}

describe('ImportDialog', () => {
  it('a backend yaml path imports with kind yaml and the stem as name', async () => {
    const imp = vi.spyOn(api, 'importProfile').mockResolvedValue(entry)
    const { onDone, user } = show()
    expect(screen.getByRole('dialog', { name: 'Import a profile' })).toBeInTheDocument()
    await user.type(screen.getByRole('textbox', { name: 'Path' }), '/srv/profiles/udp_1pkt.yaml')
    expect(screen.getByText('yaml')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Name' })).toHaveValue('udp_1pkt')
    await user.click(screen.getByRole('button', { name: 'Import' }))
    expect(imp).toHaveBeenCalledWith({ path: '/srv/profiles/udp_1pkt.yaml', kind: 'yaml', name: 'udp_1pkt' })
    expect(onDone).toHaveBeenCalledWith(entry)
  })

  it('a .py needs Inspect, then sends tunables and tunable_form', async () => {
    const inspected: Inspected = { path: '/srv/gen.py', kind: 'py', summary: [], stream_count: 1,
      tunables: [{ dest: 'size', flag: '--size', kind: 'int', default: 64, choices: null, help: 'bytes' }] }
    const insp = vi.spyOn(api, 'inspect').mockResolvedValue(inspected)
    const imp = vi.spyOn(api, 'importProfile').mockResolvedValue(entry)
    const { user } = show()
    await user.type(screen.getByRole('textbox', { name: 'Path' }), '/srv/gen.py')
    expect(screen.getByRole('button', { name: 'Import' })).toBeDisabled()
    expect(insp).not.toHaveBeenCalled()                     // never run on its own
    await user.click(screen.getByRole('button', { name: 'Inspect' }))
    expect(insp).toHaveBeenCalledWith({ path: '/srv/gen.py' })
    const size = await screen.findByRole('spinbutton', { name: /--size/ })
    await user.clear(size)
    await user.type(size, '128')
    await user.click(screen.getByRole('button', { name: 'Import' }))
    expect(imp).toHaveBeenCalledWith({ path: '/srv/gen.py', kind: 'py', tunables: ['--size', '128'],
      tunable_form: { values: { size: '128' }, extra: '' }, name: 'gen' })
  })

  it('a .py from this computer is refused with the reason', async () => {
    const up = vi.spyOn(api, 'uploadFile')
    const { user } = show()
    await user.click(screen.getByRole('radio', { name: 'From this computer' }))
    await user.upload(screen.getByLabelText('File'), new File(['print(1)'], 'gen.py'))
    expect(screen.getByRole('alert')).toHaveTextContent('A .py profile must be on the backend host: the library keeps a link to it.')
    expect(screen.getByRole('button', { name: 'Import' })).toBeDisabled()
    expect(up).not.toHaveBeenCalled()
  })

  it('an uploaded pcap sends upload_id and the wizard options', async () => {
    const up = vi.spyOn(api, 'uploadFile').mockResolvedValue({ upload_id: UPLOAD_ID, name: 'trace.pcap', size: 8 })
    const imp = vi.spyOn(api, 'importProfile').mockResolvedValue(entry)
    const { user } = show()
    await user.click(screen.getByRole('radio', { name: 'From this computer' }))
    const file = new File(['pcapdata'], 'trace.pcap')
    await user.upload(screen.getByLabelText('File'), file)
    expect(screen.getByRole('textbox', { name: 'Name' })).toHaveValue('trace')
    await user.clear(screen.getByRole('spinbutton', { name: 'Loops' }))
    await user.type(screen.getByRole('spinbutton', { name: 'Loops' }), '3')
    await user.click(screen.getByRole('button', { name: 'Import' }))
    expect(up).toHaveBeenCalledWith(file)
    expect(imp).toHaveBeenCalledWith({ upload_id: UPLOAD_ID, kind: 'pcap', pcap: { ...PCAP_DEFAULTS, loop_count: 3 }, name: 'trace' })
  })

  it('append mode sends append_to with the rev', async () => {
    const reply: DiffReply = { rev: 'r2', stream_count: 5, renamed: {}, cleared: [], warnings: [] }
    const imp = vi.spyOn(api, 'importProfile')
    // the editor's queue: it builds the body with the rev current at the write's turn
    const sent: api.ImportBody[] = []
    const send = vi.fn(async (build: (rev: string) => api.ImportBody) => { sent.push(build('r1')); return reply })
    const { onDone, user } = show({ slug: 'udp-for-benchmarks', send })
    expect(screen.getByRole('dialog', { name: 'Import streams from pcap' })).toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: 'Name' })).not.toBeInTheDocument()
    await user.type(screen.getByRole('textbox', { name: 'Path' }), '/srv/t.cap')
    await user.click(screen.getByRole('button', { name: 'Import' }))
    expect(sent).toEqual([{ path: '/srv/t.cap', kind: 'pcap', pcap: PCAP_DEFAULTS, append_to: { slug: 'udp-for-benchmarks', rev: 'r1' } }])
    expect(imp).not.toHaveBeenCalled()        // only through the queue
    expect(onDone).toHaveBeenCalledWith(reply)
  })

  it('a retry reuses the upload, except after a 404, which uploads again', async () => {
    const up = vi.spyOn(api, 'uploadFile').mockResolvedValueOnce({ upload_id: UPLOAD_ID, name: 't.pcap', size: 1 })
      .mockResolvedValueOnce({ upload_id: 'cd'.repeat(16), name: 't.pcap', size: 1 })
    const imp = vi.spyOn(api, 'importProfile')
      .mockRejectedValueOnce(new api.ApiError(422, 'profile_error', 'not a pcap'))
      .mockRejectedValueOnce(new api.ApiError(404, 'not_found', `No upload ${UPLOAD_ID} (it may have expired)`))
      .mockResolvedValueOnce(entry)
    const { onDone, user } = show()
    await user.click(screen.getByRole('radio', { name: 'From this computer' }))
    await user.upload(screen.getByLabelText('File'), new File(['x'], 't.pcap'))
    await user.click(screen.getByRole('button', { name: 'Import' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('not a pcap')
    await user.click(screen.getByRole('button', { name: 'Import' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('No upload')
    expect(up).toHaveBeenCalledTimes(1)                         // the 422 retry reused the upload
    await user.click(screen.getByRole('button', { name: 'Import' }))
    expect(up).toHaveBeenCalledTimes(2)                         // the 404 forgot it
    expect(imp).toHaveBeenLastCalledWith(expect.objectContaining({ upload_id: 'cd'.repeat(16) }))
    expect(onDone).toHaveBeenCalledWith(entry)
  })

  it('a 413 shows the server message on the one error line', async () => {
    vi.spyOn(api, 'uploadFile').mockRejectedValue(new api.ApiError(413, 'too_large', 'an upload is at most 64 MiB'))
    const imp = vi.spyOn(api, 'importProfile')
    const { onDone, user } = show()
    await user.click(screen.getByRole('radio', { name: 'From this computer' }))
    await user.upload(screen.getByLabelText('File'), new File(['x'], 'big.pcap'))
    await user.click(screen.getByRole('button', { name: 'Import' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('an upload is at most 64 MiB')
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(imp).not.toHaveBeenCalled()
    expect(onDone).not.toHaveBeenCalled()
  })

  it('blocked keeps Import disabled and says why', async () => {
    const send = vi.fn()
    render(<ImportDialog appendTo={{ slug: 'p', send }} blocked onDone={vi.fn()} onClose={vi.fn()} />)
    await userEvent.type(screen.getByRole('textbox', { name: 'Path' }), '/srv/t.pcap')
    expect(screen.getByRole('button', { name: 'Import' })).toBeDisabled()
    expect(screen.getByText("Waiting for the table's last change to be saved and read back.")).toBeInTheDocument()
  })
})
