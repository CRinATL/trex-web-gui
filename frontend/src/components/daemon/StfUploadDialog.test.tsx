import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import { NEW_STF_FORM } from './daemonForm'
import { StfRunFields } from './StfRunFields'
import { StfUploadDialog } from './StfUploadDialog'

const PROFILE = '/tmp/trex_files/tg-aaaaaaaaaaaa-p.yaml'
const PREVIEW: api.UploadPreview = {
  profile: PROFILE,
  files: [
    { template: 'a.pcap', local: '/home/u/a.pcap', remote: 'tg-111111111111-a.pcap', bytes: 2048, exists: false },
    { template: 'cap2/b.pcap', local: '/home/b.pcap', remote: 'tg-222222222222-b.pcap', bytes: 100, exists: true },
  ],
  total_bytes: 2148 + 300, yaml: 'x', yaml_remote: 'tg-aaaaaaaaaaaa-p.yaml', yaml_bytes: 300, yaml_exists: false,
  warnings: ['comments are not kept'],
}
const TARGET = { daemon: '10.0.0.5:8090', filesPath: '/tmp/trex_files' }
const onUploaded = vi.fn()
const onClose = vi.fn()
const show = () => render(<StfUploadDialog path="/home/u/p.yaml" target={TARGET} onUploaded={onUploaded} onClose={onClose} />)

beforeEach(() => {
  vi.restoreAllMocks(); onUploaded.mockClear(); onClose.mockClear()
  vi.spyOn(api, 'daemonStfUploadPreview').mockResolvedValue(PREVIEW)
})

describe('StfUploadDialog', () => {
  it('upload previews then asks first', async () => {
    const upload = vi.spyOn(api, 'daemonStfUpload').mockResolvedValue({ profile: PROFILE, pushed: ['x'], skipped: [] })
    show()
    const table = await screen.findByRole('table', { name: 'Files to upload' })
    expect(api.daemonStfUploadPreview).toHaveBeenCalledWith('/home/u/p.yaml')
    expect(within(table).getAllByRole('columnheader').map((h) => h.textContent)).toEqual(['Template', 'Local file', 'Size', 'Remote name', 'Already on the server'])
    const rows = within(table).getAllByRole('row').slice(1).map((r) => within(r).getAllByRole('cell').map((c) => c.textContent))
    expect(rows).toEqual([
      ['a.pcap', '/home/u/a.pcap', '2.0 KiB', 'tg-111111111111-a.pcap', 'no'],
      ['cap2/b.pcap', '/home/b.pcap', '100 B', 'tg-222222222222-b.pcap', 'yes'],
      ['(the profile)', '/home/u/p.yaml', '300 B', 'tg-aaaaaaaaaaaa-p.yaml', 'no'],
    ])
    expect(screen.getByText('Total: 3 files, 2.4 KiB')).toBeInTheDocument()
    expect(screen.getByRole('list', { name: 'Upload warnings' })).toHaveTextContent('comments are not kept')
    await userEvent.click(screen.getByRole('button', { name: 'Push…' }))
    expect(upload).not.toHaveBeenCalled()
    await userEvent.click(within(screen.getByRole('dialog', { name: 'Push files' })).getByRole('button', { name: 'Push' }))
    expect(upload).toHaveBeenCalledWith({ path: '/home/u/p.yaml', confirm: true, expected_yaml_remote: 'tg-aaaaaaaaaaaa-p.yaml', expected_target: '10.0.0.5:8090' })
    await vi.waitFor(() => expect(onUploaded).toHaveBeenCalledWith(PROFILE))
  })

  it('the confirm pins the daemon named when it opened, not the one shown later', async () => {
    const upload = vi.spyOn(api, 'daemonStfUpload').mockResolvedValue({ profile: PROFILE, pushed: ['x'], skipped: [] })
    const { rerender } = show()
    await screen.findByRole('table', { name: 'Files to upload' })
    await userEvent.click(screen.getByRole('button', { name: 'Push…' }))
    const confirm = screen.getByRole('dialog', { name: 'Push files' })
    expect(confirm).toHaveTextContent('on 10.0.0.5:8090?')
    rerender(<StfUploadDialog path="/home/u/p.yaml" target={{ daemon: '10.0.0.9:8090', filesPath: '/tmp/trex_files' }} onUploaded={onUploaded} onClose={onClose} />)
    await userEvent.click(within(screen.getByRole('dialog', { name: 'Push files' })).getByRole('button', { name: 'Push' }))
    expect(upload).toHaveBeenCalledWith(expect.objectContaining({ expected_target: '10.0.0.5:8090' }))
  })

  it('upload confirm says files cannot be deleted', async () => {
    show()
    await userEvent.click(await screen.findByRole('button', { name: 'Push…' }))
    // only what is not on the server is written: a.pcap and the profile, 2048 + 300 bytes
    expect(screen.getByRole('dialog', { name: 'Push files' })).toHaveTextContent(
      'Write 2 files (2.3 KiB) to /tmp/trex_files on 10.0.0.5:8090? The daemon cannot delete them; they stay until the host clears /tmp/trex_files.')
  })

  it('the confirm says 1 file for one file', async () => {
    vi.spyOn(api, 'daemonStfUploadPreview').mockResolvedValue({ ...PREVIEW, files: PREVIEW.files.map((f) => ({ ...f, exists: true })) })
    show()
    await userEvent.click(await screen.findByRole('button', { name: 'Push…' }))
    expect(screen.getByRole('dialog', { name: 'Push files' })).toHaveTextContent(
      'Write 1 file (300 B) to /tmp/trex_files on 10.0.0.5:8090? The daemon cannot delete them;')
  })

  it('a refused plan shows the reason and offers no Push', async () => {
    vi.spyOn(api, 'daemonStfUploadPreview').mockRejectedValue(new api.ApiError(409, 'invalid_profile', "template 'gone.pcap': no such file"))
    show()
    expect(await screen.findByRole('alert')).toHaveTextContent("template 'gone.pcap': no such file")
    expect(screen.getByRole('button', { name: 'Push…' })).toBeDisabled()
  })

  it('a refused push keeps the dialog open with the reason', async () => {
    vi.spyOn(api, 'daemonStfUpload').mockRejectedValue(new api.ApiError(502, 'daemon_fault', '-33: nope'))
    show()
    await userEvent.click(await screen.findByRole('button', { name: 'Push…' }))
    await userEvent.click(within(screen.getByRole('dialog', { name: 'Push files' })).getByRole('button', { name: 'Push' }))
    expect(await screen.findByText('-33: nope')).toBeInTheDocument()
    expect(onUploaded).not.toHaveBeenCalled()
  })

  it('with everything already on the server it writes nothing and uses the profile', async () => {
    const upload = vi.spyOn(api, 'daemonStfUpload')
    vi.spyOn(api, 'daemonStfUploadPreview').mockResolvedValue({ ...PREVIEW, files: PREVIEW.files.map((f) => ({ ...f, exists: true })), yaml_exists: true })
    show()
    expect(await screen.findByText('Everything is already on the server.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Use it' }))
    expect(onUploaded).toHaveBeenCalledWith(PROFILE)
    expect(upload).not.toHaveBeenCalled()
  })

  it('uploaded profile becomes the selection', async () => {
    vi.spyOn(api, 'fsList').mockResolvedValue({ path: '/home/u', parent: null, entries: [{ name: 'p.yaml', dir: false, size: 10 }], truncated: false })
    vi.spyOn(api, 'daemonStfUpload').mockResolvedValue({ profile: PROFILE, pushed: ['a'], skipped: [] })
    vi.spyOn(api, 'daemonStfProfiles').mockImplementation((source) => Promise.resolve({ source, files: source === 'uploaded' ? [PROFILE] : ['cap2/sfr2.yaml'] }))
    vi.spyOn(api, 'daemonStfProfile').mockImplementation((path) => Promise.resolve({ path, duration: 10, templates: ['/tmp/trex_files/tg-111111111111-a.pcap'], errors: [], warnings: [] }))
    function Harness() {
      const [form, setForm] = useState(NEW_STF_FORM)
      return <><StfRunFields form={form} onChange={(p) => setForm((f) => ({ ...f, ...p }))} cfgPath="/etc/trex_cfg.yaml" pushed={null}
        serverPorts={null} onValid={() => undefined} upload={TARGET} onModal={() => undefined} /><output aria-label="profile">{form.profile}</output></>
    }
    render(<Harness />)
    await userEvent.click(screen.getByRole('button', { name: 'Upload…' }))
    await userEvent.click(await screen.findByRole('button', { name: 'p.yaml' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Push…' }))
    await userEvent.click(within(screen.getByRole('dialog', { name: 'Push files' })).getByRole('button', { name: 'Push' }))
    await vi.waitFor(() => expect(screen.getByLabelText('profile')).toHaveTextContent(PROFILE))
    expect(screen.queryByRole('dialog', { name: 'Upload a stateful profile' })).toBeNull()
    expect(screen.getByRole('combobox', { name: 'Source' })).toHaveDisplayValue('Uploaded')
    await vi.waitFor(() => expect(screen.getByRole('combobox', { name: 'Profile' })).toHaveValue(PROFILE))
    expect(api.daemonStfProfile).toHaveBeenCalledWith(PROFILE)
  })
})
