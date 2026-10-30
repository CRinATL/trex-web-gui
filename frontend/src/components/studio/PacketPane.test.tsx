import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import type { BuildResult, Definitions, PacketDoc, StreamPage, StreamRow } from '../../api'
import * as download from '../../download'
import buildFixture from '../../__fixtures__/packet/buildUdp.json'
import defsFixture from '../../__fixtures__/packet/definitions.json'
import streams from '../../__fixtures__/studioStreams.json'
import { PacketPane } from './PacketPane'
import { ProfileWriter } from './studioWriter'
import { undoToggle, UndoHistory } from './streamUndo'

afterEach(() => { cleanup(); vi.restoreAllMocks() })
const defs = (defsFixture as unknown as { definitions: Definitions }).definitions
const udp = buildFixture.udp as unknown as BuildResult
const row = (streams.udp_for_benchmarks as unknown as StreamPage).rows[0]
const withVm = (n: number): StreamRow => ({ ...row, stream: { ...row.stream, vm: { instructions: Array.from({ length: n }, () => ({ type: 'fix_checksum_ipv4' })) } } })

describe('PacketPane', () => {
  it('shows length with FCS and the instruction count', () => {
    render(<PacketPane slug="s" row={withVm(3)} />)
    expect(screen.getByText('124 bytes (128 with FCS)')).toBeInTheDocument()
    expect(screen.getByText('Field engine: 3 instructions')).toBeInTheDocument()
    expect(screen.getByText(/Ether \/ IP \/ UDP 16\.0\.0\.1:1025/)).toBeInTheDocument()
    cleanup()
    render(<PacketPane slug="s" row={withVm(1)} />)
    expect(screen.getByText('Field engine: 1 instruction')).toBeInTheDocument()
  })

  it('pcap export downloads <slug>-<index>.pcap holding the packet', async () => {
    const dl = vi.spyOn(download, 'downloadBlob').mockImplementation(() => undefined)
    const user = userEvent.setup()
    render(<PacketPane slug="my-profile" row={{ ...row, index: 4 }} />)
    await user.click(screen.getByRole('button', { name: 'Export ▾' }))
    expect(screen.getByRole('menuitem', { name: 'YAML' })).toHaveAttribute('href', '/api/studio/profiles/my-profile/export?format=yaml&stream=4')
    await user.click(screen.getByRole('menuitem', { name: 'pcap' }))
    expect(dl).toHaveBeenCalledTimes(1)
    const [name, blob] = dl.mock.calls[0]
    expect(name).toBe('my-profile-4.pcap')
    const bytes = new Uint8Array(await blob.arrayBuffer())
    const packet = Uint8Array.from(atob(row.stream.packet.binary), (c) => c.charCodeAt(0))
    expect(bytes.length).toBe(24 + 16 + 124)
    expect(Array.from(bytes.slice(40))).toEqual(Array.from(packet))
  })

  it('a stream without a usable packet shows its summary and disables View packet and the pcap export', async () => {
    const user = userEvent.setup()
    render(<PacketPane slug="s" row={{ ...row, length: null, summary: { proto: '?', info: 'no usable packet (not base64)' } }} />)
    expect(screen.getByText(/no usable packet \(not base64\)/)).toBeInTheDocument()
    expect(screen.queryByText(/with FCS/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'View packet…' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Export ▾' }))
    expect(screen.getByRole('menuitem', { name: 'pcap' })).toBeDisabled()
    expect(screen.getByRole('menuitem', { name: 'YAML' })).toBeInTheDocument()
  })

  it('View packet opens the viewer on the stream packet and closes with Escape', async () => {
    vi.spyOn(api, 'packetTree').mockRejectedValue(new Error('no service in this test'))
    const user = userEvent.setup()
    render(<PacketPane slug="s" row={row} />)
    await user.click(screen.getByRole('button', { name: 'View packet…' }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('Escape closes the export menu, and so does a press outside it', async () => {
    vi.spyOn(download, 'downloadBlob').mockImplementation(() => undefined)     // the pcap item below would navigate jsdom
    const user = userEvent.setup()
    render(<PacketPane slug="s" row={row} />)
    await user.click(screen.getByRole('button', { name: 'Export ▾' }))
    expect(screen.getByRole('menu')).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Export ▾' }))
    await user.click(document.body)
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Export ▾' }))
    await user.click(screen.getByRole('menuitem', { name: 'pcap' }))      // a press inside does not close it before the item runs
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })

  it('Edit packet is off without a writer', () => {
    render(<PacketPane slug="s" row={row} />)
    expect(screen.getByRole('button', { name: 'Edit packet' })).toBeDisabled()
  })
})

const udpDoc = (): PacketDoc => ({
  fileType: 'trex-packet-editor', version: '1.0.0', metadata: { caption: '' }, fePrarameters: { cache_size: '1000' }, feInstructions: [],
  packet: [{ id: 'Ether', fields: [] }, { id: 'IP', fields: [] }, { id: 'UDP', fields: [] }, { id: 'Raw', fields: [] }],
})
const built = (d: PacketDoc): BuildResult => ({
  ...udp, data: d.packet.map((l, i) => ({ ...(udp.data.find((x) => x.id === l.id) ?? { ...udp.data[3], id: l.id, real_id: l.id }), offset: udp.data[Math.min(i, 3)].offset })),
})
const reply = (rev: string, n = 1): api.DiffReply => ({ rev, stream_count: n, renamed: {}, cleared: [], warnings: [] })
const NEW = { packet: { binary: 'TkVXUEFDS0VU', meta: '' }, vm: { instructions: [{ type: 'fix_checksum_ipv4' }] } }

describe('PacketPane editing', () => {
  beforeEach(() => {
    vi.spyOn(api, 'packetDefinitions').mockResolvedValue(defs)
    vi.spyOn(api, 'buildPacket').mockImplementation(async (d) => built(d))
    vi.spyOn(api, 'payloadClasses').mockResolvedValue({ classes: [] })
    vi.spyOn(api, 'docFromBinary').mockResolvedValue({ doc: udpDoc(), warnings: [] })
    vi.spyOn(api, 'listPresets').mockResolvedValue({ presets: [] })
    vi.spyOn(window, 'confirm')
  })
  const setup = (over: Partial<StreamRow> = {}, pageKeys?: () => void) => {
    const writer = new ProfileWriter('s', 'r0')
    const history = new UndoHistory()
    const onSaved = vi.fn()
    const onDirty = vi.fn()
    const r = { ...row, ...over }
    render(<div onKeyDown={pageKeys}><PacketPane slug="s" row={r} writer={writer} history={history} onSaved={onSaved} onDirty={onDirty} /></div>)
    return { writer, history, onSaved, onDirty, row: r, user: userEvent.setup() }
  }
  const startEdit = async (user: ReturnType<typeof userEvent.setup>) => {
    await user.click(screen.getByRole('button', { name: 'Edit packet' }))
    await screen.findByRole('tab', { name: 'Packet' })
    await waitFor(() => expect(document.querySelector('.layer-list')).not.toBeNull())
  }
  async function editTtl(user: ReturnType<typeof userEvent.setup>, value: string) {
    if (!screen.queryByRole('textbox', { name: 'TTL' })) await user.click(within(document.querySelector('.layer-list') as HTMLElement).getByRole('button', { name: 'IP' }))
    const box = await screen.findByRole('textbox', { name: 'TTL' })
    await user.clear(box)
    await user.type(box, `${value}{Enter}`)
  }
  // The reads a Save makes at its turn: the stream as stored, and its sidecar entry, both at the writer's rev.
  const server = (stream = row.stream, rev = 'r0') => {
    vi.spyOn(api, 'getStreams').mockResolvedValue({ rev, stream_count: 1, names: [null], offset: 0, rows: [{ ...row, stream }], warnings: [] })
    vi.spyOn(api, 'streamSidecar').mockResolvedValue({ rev, entry: {} })
  }

  it('Edit packet opens the embedded editor with Packet and Field engine tabs', async () => {
    const { user } = setup()
    await startEdit(user)
    expect(api.docFromBinary).toHaveBeenCalledWith(row.stream.packet.binary, { instructions: [] })
    expect(screen.getByRole('tab', { name: 'Field engine' })).toBeInTheDocument()
    for (const name of ['Presets…', 'Cancel', 'Save']) expect(screen.getByRole('button', { name })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Edit packet' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'View packet…' })).not.toBeInTheDocument()
  })

  it('Cancel restores the information view', async () => {
    const { user, onDirty } = setup()
    await startEdit(user)
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.getByRole('button', { name: 'Edit packet' })).toBeInTheDocument()
    expect(screen.getByText('124 bytes (128 with FCS)')).toBeInTheDocument()
    expect(onDirty).not.toHaveBeenCalledWith(true)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('a packet with unsaved edits has the edited tag beside Save (RC1-31)', async () => {
    const { user } = setup()
    await startEdit(user)
    expect(screen.queryByText('edited')).not.toBeInTheDocument()
    await editTtl(user, '9')
    const bar = within(screen.getByRole('toolbar', { name: 'Edit packet' }))
    expect(bar.getByText('edited')).toHaveClass('edited-tag')
  })

  it('Cancel with unsaved edits asks in the app dialog, and Keep editing keeps them', async () => {
    const { user, onDirty } = setup()
    await startEdit(user)
    await editTtl(user, '9')
    await waitFor(() => expect(onDirty).toHaveBeenLastCalledWith(true))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(await screen.findByRole('dialog', { name: 'Discard the packet edits?' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Keep editing' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Packet' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    await user.click(await screen.findByRole('button', { name: 'Discard' }))
    expect(screen.getByRole('button', { name: 'Edit packet' })).toBeInTheDocument()
    expect(onDirty).toHaveBeenLastCalledWith(false)
    expect(window.confirm).not.toHaveBeenCalled()
  })

  it('Save writes one diff through the writer and pushes an undo entry', async () => {
    const { user, writer, history, onSaved } = setup()
    server()
    vi.spyOn(api, 'toStream').mockResolvedValue(NEW)
    const put = vi.spyOn(api, 'putStreams').mockResolvedValue(reply('r1'))
    await startEdit(user)
    await editTtl(user, '9')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1))
    expect(put).toHaveBeenCalledTimes(1)
    const [slug, diff] = put.mock.calls[0]
    expect(slug).toBe('s')
    expect(diff.rev).toBe('r0')
    expect(diff.changed).toEqual({ 0: { packet: { binary: NEW.packet.binary, meta: '' }, vm: NEW.vm } })
    const doc = (diff.sidecar as Record<string, { editor: { doc: PacketDoc; binary: string } }>)[0].editor
    expect(doc.binary).toBe(NEW.packet.binary)
    expect(doc.doc.packet[1].fields).toEqual([{ id: 'ttl', value: 9 }])
    expect(writer.rev).toBe('r1')
    // one entry, whose undo puts the stored stream and its (empty) entry back and whose redo writes the new ones again
    const entry = history.nextUndo()!
    expect(entry.label).toBe('editing stream 0')
    expect(entry.undo('r2')).toEqual({ rev: 'r2', changed: { 0: { packet: row.stream.packet, vm: row.stream.vm } }, sidecar: { 0: {} } })
    expect(entry.redo('r2')).toEqual({ rev: 'r2', changed: { 0: diff.changed![0] }, sidecar: { 0: diff.sidecar![0] } })
    expect(await screen.findByRole('status')).toHaveTextContent('Saved.')
    expect(screen.getByRole('button', { name: 'Edit packet' })).toBeInTheDocument()
  })

  it('a Save whose reads were not at the writer rev clears the history instead of pushing', async () => {
    const { user, history } = setup()
    history.push(undoToggle(0, true))
    server(row.stream, 'older')          // the page the "before" would come from lags the writer
    vi.spyOn(api, 'toStream').mockResolvedValue(NEW)
    vi.spyOn(api, 'putStreams').mockResolvedValue(reply('r1'))
    await startEdit(user)
    await editTtl(user, '9')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await screen.findByText(/Saved\./)
    expect(history.nextUndo()).toBeUndefined()
  })

  it('a MAC edit and a preset set the flags in the diff', async () => {
    const { user } = setup()
    server()
    vi.spyOn(api, 'toStream').mockResolvedValue(NEW)
    const put = vi.spyOn(api, 'putStreams').mockResolvedValue(reply('r1'))
    await startEdit(user)
    const box = await screen.findByRole('textbox', { name: 'Source' })      // the first layer starts open
    await user.clear(box)
    await user.type(box, '00:00:00:00:00:09{Enter}')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await screen.findByText(/Saved\./)
    expect(put.mock.calls[0][1].changed![0].flags).toBe(row.stream.flags | 1)
  })

  it('a 422 invalid_stream shows its field on the one error line and keeps the editor', async () => {
    const { user, history } = setup()
    server()
    vi.spyOn(api, 'toStream').mockResolvedValue(NEW)
    vi.spyOn(api, 'putStreams').mockRejectedValue(new api.ApiError(422, 'invalid_stream', 'stream 0: over the limit', { index: 0, field: 'packet' }))
    await startEdit(user)
    await editTtl(user, '9')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    const alerts = await screen.findAllByRole('alert')
    expect(alerts).toHaveLength(1)
    expect(alerts[0]).toHaveTextContent('stream 0: over the limit (field: packet)')
    expect(screen.getByRole('tab', { name: 'Packet' })).toBeInTheDocument()
    expect(history.nextUndo()).toBeUndefined()
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled()     // another Save is possible
  })

  it('a 422 from to-stream shows too, and nothing is written', async () => {
    const { user } = setup()
    const put = vi.spyOn(api, 'putStreams')
    vi.spyOn(api, 'toStream').mockRejectedValue(new api.ApiError(422, 'invalid_stream', 'the packet is 9300 bytes, over 9238', { field: 'packet' }))
    await startEdit(user)
    await editTtl(user, '9')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('9300 bytes')
    expect(put).not.toHaveBeenCalled()
  })

  it('a 409 stale ends the writer with no line of its own', async () => {
    const { user, writer } = setup()
    server()
    vi.spyOn(api, 'toStream').mockResolvedValue(NEW)
    vi.spyOn(api, 'putStreams').mockRejectedValue(new api.ApiError(409, 'stale', 'The profile changed elsewhere.'))
    await startEdit(user)
    await editTtl(user, '9')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(writer.getState()).toBe('stale'))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('a stream that is no longer the one the editor opened is not overwritten', async () => {
    const { user } = setup()
    server({ ...row.stream, packet: { binary: 'AAAA', meta: '' } })      // changed (or moved) since
    vi.spyOn(api, 'toStream').mockResolvedValue(NEW)
    const put = vi.spyOn(api, 'putStreams')
    await startEdit(user)
    await editTtl(user, '9')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/stream changed since the editor was opened/)
    expect(put).not.toHaveBeenCalled()
  })

  it('a built packet is dropped when the document changed while it was being built', async () => {
    const { user } = setup()
    server()
    let finish: (v: typeof NEW) => void = () => undefined
    vi.spyOn(api, 'toStream').mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    const put = vi.spyOn(api, 'putStreams')
    await startEdit(user)
    await editTtl(user, '9')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await editTtl(user, '10')
    finish(NEW)
    expect(await screen.findByRole('alert')).toHaveTextContent(/changed while it was being built/)
    expect(put).not.toHaveBeenCalled()
  })

  describe('Presets…', () => {
    const vlan = { id: 'vlan', name: 'VLAN', defaults: { tags: [{ vlan: 100, prio: 0 }] } }
    const presetDoc = { ...udpDoc(), metadata: { caption: 'preset' } }
    beforeEach(() => {
      vi.spyOn(api, 'listPresets').mockResolvedValue({ presets: [vlan] })
      vi.spyOn(api, 'buildPreset').mockResolvedValue({ doc: presetDoc, flags: 6 })
    })

    it('Escape closes the dialog', async () => {
      const { user } = setup()
      await startEdit(user)
      await user.click(screen.getByRole('button', { name: 'Presets…' }))
      expect(await screen.findByRole('dialog', { name: 'Presets' })).toBeInTheDocument()
      await user.keyboard('{Escape}')
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })

    it('a preset replaces the document and its flags win on Save', async () => {
      const { user } = setup()
      server()
      vi.spyOn(api, 'toStream').mockResolvedValue(NEW)
      const put = vi.spyOn(api, 'putStreams').mockResolvedValue(reply('r1'))
      await startEdit(user)
      await user.click(screen.getByRole('button', { name: 'Presets…' }))
      await user.click(await screen.findByRole('button', { name: 'Open in editor' }))
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
      await user.click(screen.getByRole('button', { name: 'Save' }))
      await screen.findByText(/Saved\./)
      expect(put.mock.calls[0][1].changed![0].flags).toBe(6)
      expect((put.mock.calls[0][1].sidecar as Record<string, { editor: { doc: PacketDoc } }>)[0].editor.doc).toEqual(presetDoc)
    })

    it('over unsaved edits a preset asks first, and Escape closes only the question', async () => {
      const { user } = setup()
      await startEdit(user)
      await editTtl(user, '9')
      await user.click(screen.getByRole('button', { name: 'Presets…' }))
      await user.click(await screen.findByRole('button', { name: 'Open in editor' }))
      expect(await screen.findByRole('dialog', { name: 'Discard the packet edits?' })).toBeInTheDocument()
      await user.keyboard('{Escape}')
      expect(screen.queryByRole('dialog', { name: 'Discard the packet edits?' })).not.toBeInTheDocument()
      expect(screen.getByRole('dialog', { name: 'Presets' })).toBeInTheDocument()
      await user.keyboard('{Escape}')
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })

    it('Discard takes the preset', async () => {
      const { user, onDirty } = setup()
      await startEdit(user)
      await editTtl(user, '9')
      await user.click(screen.getByRole('button', { name: 'Presets…' }))
      await user.click(await screen.findByRole('button', { name: 'Open in editor' }))
      await user.click(await screen.findByRole('button', { name: 'Discard' }))
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
      await waitFor(() => expect(vi.mocked(api.buildPacket)).toHaveBeenLastCalledWith(presetDoc))
      expect(onDirty).toHaveBeenLastCalledWith(true)       // a preset is a change from the stored packet
    })
  })

  it('a key inside a dialog does not reach the page, except Escape, and Ctrl+S is kept from the browser', async () => {
    const pageKeys = vi.fn()
    const { user } = setup({}, pageKeys)
    await startEdit(user)
    await user.click(screen.getByRole('button', { name: 'Presets…' }))
    const dialog = await screen.findByRole('dialog', { name: 'Presets' })
    pageKeys.mockClear()
    expect(fireEvent.keyDown(dialog, { key: 'z', ctrlKey: true })).toBe(true)
    expect(fireEvent.keyDown(dialog, { key: 's', ctrlKey: true })).toBe(false)      // default prevented
    expect(pageKeys).not.toHaveBeenCalled()
    expect(fireEvent.contextMenu(dialog)).toBe(true)
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(pageKeys).toHaveBeenCalled()
  })
})
