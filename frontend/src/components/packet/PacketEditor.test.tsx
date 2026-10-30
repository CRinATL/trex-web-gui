import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import type { BuildResult, Definitions, PacketDoc, PacketTree } from '../../api'
import buildFixture from '../../__fixtures__/packet/buildUdp.json'
import defsFixture from '../../__fixtures__/packet/definitions.json'
import { PacketEditor } from './PacketEditor'

vi.mock('../../api', async (orig) => ({ ...(await orig<typeof import('../../api')>()), buildPacket: vi.fn(), reconstructEdit: vi.fn(), packetDefinitions: vi.fn(), payloadClasses: vi.fn() }))

const defs = (defsFixture as unknown as { definitions: Definitions }).definitions
const udp = buildFixture.udp as unknown as BuildResult
const doc = (): PacketDoc => ({
  fileType: 'trex-packet-editor', version: '1.0.0', metadata: { caption: '' }, fePrarameters: {}, feInstructions: [],
  packet: [{ id: 'Ether', fields: [] }, { id: 'IP', fields: [] }, { id: 'UDP', fields: [] }, { id: 'Raw', fields: [] }],
})
const ids = (d: PacketDoc) => d.packet.map((l) => l.id)
const ttlOf = (d: PacketDoc) => d.packet[1].fields.find((f) => f.id === 'ttl')?.value

// The fixture's build for any document, with one layer per document layer (a layer it has no data for copies Raw).
const built = (d: PacketDoc): BuildResult => ({
  ...udp, data: d.packet.map((l, i) => ({ ...(udp.data.find((x) => x.id === l.id) ?? { ...udp.data[3], id: l.id, real_id: l.id }), offset: udp.data[Math.min(i, 3)].offset })),
})

beforeEach(() => {
  vi.mocked(api.packetDefinitions).mockResolvedValue(defs)
  vi.mocked(api.buildPacket).mockImplementation(async (d) => built(d))
  vi.mocked(api.payloadClasses).mockResolvedValue({ classes: [] })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

// A parent that keeps the editor's document, as the real one does.
function Host({ onDocChange, mode = 'standalone', initial = doc }: { onDocChange: (d: PacketDoc) => void; mode?: 'standalone' | 'embedded'; initial?: () => PacketDoc }) {
  const [d, setD] = useState(initial)
  return <><button type="button">outside</button><PacketEditor doc={d} mode={mode} onDocChange={(n) => { setD(n); onDocChange(n) }} /></>
}
// A pane title; the breadcrumb has buttons of the same names.
const list = () => document.querySelector('.layer-list') as HTMLElement
const pane = (name: string) => within(list()).getByRole('button', { name })
const ready = () => waitFor(() => expect(list()).not.toBeNull())

async function editTtl(value: string) {
  await ready()
  await userEvent.click(pane('IP'))
  const box = await screen.findByRole('textbox', { name: 'TTL' })
  await userEvent.clear(box)
  await userEvent.type(box, `${value}{Enter}`)
}

describe('PacketEditor', () => {
  it('Ctrl+Z and Ctrl+Y undo and redo while focus is inside', async () => {
    const onDocChange = vi.fn()
    render(<Host onDocChange={onDocChange} />)
    await editTtl('9')
    await waitFor(() => expect(onDocChange).toHaveBeenCalledTimes(1))
    expect(ttlOf(onDocChange.mock.calls[0][0])).toBe(9)
    await userEvent.keyboard('{Control>}z{/Control}')
    await waitFor(() => expect(onDocChange).toHaveBeenCalledTimes(2))
    expect(ttlOf(onDocChange.mock.calls[1][0])).toBeUndefined()
    await userEvent.keyboard('{Control>}y{/Control}')
    await waitFor(() => expect(onDocChange).toHaveBeenCalledTimes(3))
    expect(ttlOf(onDocChange.mock.calls[2][0])).toBe(9)
    await userEvent.keyboard('{Control>}z{/Control}')
    await userEvent.keyboard('{Control>}{Shift>}z{/Shift}{/Control}')   // Ctrl+Shift+Z redoes too
    await waitFor(() => expect(onDocChange).toHaveBeenCalledTimes(5))
    expect(ttlOf(onDocChange.mock.calls[4][0])).toBe(9)
  })

  it('the toolbar Undo and Redo follow the history', async () => {
    render(<Host onDocChange={() => undefined} />)
    expect(await screen.findByRole('button', { name: 'Undo' })).toBeDisabled()
    await editTtl('9')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Undo' })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: 'Undo' }))
    expect(screen.getByRole('button', { name: 'Redo' })).toBeEnabled()
  })

  it('Ctrl+D deletes the expanded layer, never the first', async () => {
    const onDocChange = vi.fn()
    render(<Host onDocChange={onDocChange} />)
    await ready()
    await userEvent.click(pane('IP'))
    await userEvent.keyboard('{Control>}d{/Control}')
    await waitFor(() => expect(onDocChange).toHaveBeenCalledTimes(1))
    expect(ids(onDocChange.mock.calls[0][0])).toEqual(['Ether', 'UDP', 'Raw'])
    // the first layer: collapse it and open it again so that it is the one just opened
    await userEvent.click(pane('Ether'))
    await userEvent.click(pane('Ether'))
    expect(pane('Ether')).toHaveAttribute('aria-expanded', 'true')
    await userEvent.keyboard('{Control>}d{/Control}')
    expect(onDocChange).toHaveBeenCalledTimes(1)
    expect(pane('Ether')).toBeInTheDocument()
  })

  it('keys do nothing while focus is outside', async () => {
    const onDocChange = vi.fn()
    render(<Host onDocChange={onDocChange} />)
    await editTtl('9')
    await waitFor(() => expect(onDocChange).toHaveBeenCalledTimes(1))
    await userEvent.click(screen.getByRole('button', { name: 'outside' }))
    await userEvent.keyboard('{Control>}z{/Control}')
    await userEvent.keyboard('{Control>}d{/Control}')
    expect(onDocChange).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Undo' })).toBeEnabled()
  })

  it('adding a layer collapses the others and opens the new one', async () => {
    const onDocChange = vi.fn()
    render(<Host onDocChange={onDocChange} />)
    await ready()
    await userEvent.click(pane('IP'))
    await userEvent.type(screen.getByRole('combobox', { name: 'Add layer' }), 'TCP{Enter}')
    await waitFor(() => expect(pane('TCP')).toHaveAttribute('aria-expanded', 'true'))
    for (const other of ['Ether', 'IP', 'UDP', 'Raw']) expect(pane(other)).toHaveAttribute('aria-expanded', 'false')
    expect(ids(onDocChange.mock.calls[0][0]).at(-1)).toBe('TCP')
  })

  it('keeps the open panes when a rebuild arrives', async () => {
    render(<Host onDocChange={() => undefined} />)
    await ready()
    await userEvent.click(pane('UDP'))
    await userEvent.click(pane('IP'))
    const box = await screen.findByRole('textbox', { name: 'TTL' })
    await userEvent.clear(box)
    await userEvent.type(box, '9{Enter}')
    await waitFor(() => expect(api.buildPacket).toHaveBeenCalledTimes(2))
    expect(pane('IP')).toHaveAttribute('aria-expanded', 'true')
    expect(pane('UDP')).toHaveAttribute('aria-expanded', 'true')
    expect(pane('Raw')).toHaveAttribute('aria-expanded', 'false')
  })

  it('a field commit asks for no payload classes', async () => {
    render(<Host onDocChange={() => undefined} />)
    await ready()
    await waitFor(() => expect(api.payloadClasses).toHaveBeenCalledTimes(1))
    await editTtl('9')
    await waitFor(() => expect(api.buildPacket).toHaveBeenCalledTimes(2))
    expect(api.payloadClasses).toHaveBeenCalledTimes(1)
  })

  it('a rejected value stays in the field, red, with the message', async () => {
    vi.mocked(api.buildPacket).mockImplementation(async (d) => (ttlOf(d) === 9 ? { binary: null, data: [], field_engine: null, error: 'ValueError: no 9' } : built(d)))
    render(<Host onDocChange={() => undefined} />)
    await editTtl('9')
    const box = await screen.findByRole('textbox', { name: 'TTL' })
    await waitFor(() => expect(box).toHaveAttribute('aria-invalid', 'true'))
    expect(box).toHaveValue('9')
    expect(within(box.closest('.field-row') as HTMLElement).getByRole('alert')).toHaveTextContent('ValueError: no 9')
    expect(pane('IP')).toBeInTheDocument()   // the tree keeps the last good build
  })

  it('viewer mode has no inputs', async () => {
    const tree = { binary: udp.binary, data: udp.data } as unknown as PacketTree
    const { container } = render(<PacketEditor doc={doc()} mode="viewer" tree={tree} onDocChange={() => undefined} />)
    await userEvent.click(screen.getByRole('button', { name: 'Expand all' }))
    expect(screen.getByText('52:52:18:89:91:ea')).toBeInTheDocument()
    expect(container.querySelectorAll('input, select, textarea')).toHaveLength(0)
    expect(screen.queryByRole('tab', { name: 'Field engine' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Undo' })).toBeNull()
    expect(api.buildPacket).not.toHaveBeenCalled()
    expect(api.packetDefinitions).not.toHaveBeenCalled()
  })

  it('has the Packet and Field engine tabs', async () => {
    render(<Host onDocChange={() => undefined} />)
    expect(await screen.findByRole('tab', { name: 'Packet' })).toHaveAttribute('aria-selected', 'true')
    await userEvent.click(screen.getByRole('tab', { name: 'Field engine' }))
    expect(screen.getByRole('tab', { name: 'Field engine' })).toHaveAttribute('aria-selected', 'true')
  })

  it('FE templates menu adds the instructions and opens the FE tab', async () => {
    const onDocChange = vi.fn()
    render(<Host onDocChange={onDocChange} />)
    await ready()
    await userEvent.click(pane('IP'))
    await userEvent.pointer({ keys: '[MouseRight]', target: await screen.findByText('TTL') })
    await userEvent.click(screen.getByRole('menuitem', { name: 'Field engine: Simple variable' }))
    await waitFor(() => expect(onDocChange).toHaveBeenCalledTimes(1))
    const added = onDocChange.mock.calls[0][0].feInstructions
    expect(added.map((i: { id: string }) => i.id)).toEqual(['STLVmFlowVar', 'STLVmWrFlowVar'])
    expect(added[0].parameters.name).toBe('IP_ttl')
    expect(added[1].parameters).toMatchObject({ fv_name: 'IP_ttl', pkt_offset: 'IP.ttl' })
    expect(screen.getByRole('tab', { name: 'Field engine' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('list', { name: 'Field Engine structure' })).toHaveTextContent('STLVmWrFlowVar (IP_ttl)')
  })

  it('Ctrl+D does nothing while the Field engine tab shows', async () => {
    const onDocChange = vi.fn()
    render(<Host onDocChange={onDocChange} />)
    await ready()
    await userEvent.click(pane('IP'))
    await userEvent.click(screen.getByRole('tab', { name: 'Field engine' }))
    await userEvent.keyboard('{Control>}d{/Control}')
    await userEvent.click(screen.getByRole('tab', { name: 'Packet' }))
    expect(onDocChange).not.toHaveBeenCalled()
    expect(pane('IP')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Undo' })).toBeDisabled()
  })

  // The IP ttl is byte 22 of the fixture's frame (Ether 14 + 8).
  const withTtl = (d: PacketDoc, ttl: number): PacketDoc => ({ ...d, packet: d.packet.map((l) => (l.id === 'IP' ? { ...l, fields: [{ id: 'ttl', value: ttl }] } : l)) })
  async function typeByte(i: number, hex: string) {
    await userEvent.click(await screen.findByRole('button', { name: new RegExp(`^byte ${i}: `) }))
    await userEvent.keyboard(hex)
  }

  it('a byte edit sends the edited field to reconstruct and applies the returned doc', async () => {
    const edited = withTtl(doc(), 0x3c)
    vi.mocked(api.reconstructEdit).mockResolvedValue({ doc: edited, warnings: [] })
    const onDocChange = vi.fn()
    render(<Host onDocChange={onDocChange} />)
    await typeByte(22, '3c')
    await waitFor(() => expect(api.reconstructEdit).toHaveBeenCalledTimes(1))
    const [sent, binary, field] = vi.mocked(api.reconstructEdit).mock.calls[0]
    expect(sent).toEqual(doc())
    expect(field).toEqual({ layer: 1, field: 'ttl' })
    const was = Uint8Array.from(atob(udp.binary!), (c) => c.charCodeAt(0))
    const now = Uint8Array.from(atob(binary), (c) => c.charCodeAt(0))
    expect(now[22]).toBe(0x3c)
    expect(now.length).toBe(was.length)
    expect(Array.from(now.filter((_, i) => i !== 22))).toEqual(Array.from(was.filter((_, i) => i !== 22)))
    await waitFor(() => expect(onDocChange).toHaveBeenCalledWith(edited))
    expect(api.buildPacket).toHaveBeenLastCalledWith(edited)   // applied: rebuilt
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('a byte in no field is sent with no edited field', async () => {
    vi.mocked(api.reconstructEdit).mockResolvedValue({ doc: doc(), warnings: [] })
    render(<Host onDocChange={() => undefined} />)
    await typeByte(14, '46')   // IP version and ihl are bit fields with no byte range: byte 14 is in no field
    await waitFor(() => expect(api.reconstructEdit).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.reconstructEdit).mock.calls[0][2]).toBeNull()
  })

  it('a fallback warning from reconstruct is shown', async () => {
    const warning = 'the byte edit re-derived the packet from its bytes: generated and expression values became literal values'
    vi.mocked(api.reconstructEdit).mockResolvedValue({ doc: withTtl(doc(), 0x3c), warnings: [warning] })
    render(<Host onDocChange={() => undefined} />)
    await typeByte(22, '3c')
    expect(await screen.findByRole('status')).toHaveTextContent(warning)
  })

  it('a refused byte edit is shown and changes nothing', async () => {
    vi.mocked(api.reconstructEdit).mockRejectedValue(new Error('Protocol id inconsistent'))
    const onDocChange = vi.fn()
    render(<Host onDocChange={onDocChange} />)
    await typeByte(22, '3c')
    expect(await screen.findByRole('alert')).toHaveTextContent('Byte edit failed: Protocol id inconsistent')
    expect(onDocChange).not.toHaveBeenCalled()
  })

  it('a fallen-back byte edit is undone in one step', async () => {
    const fallen: PacketDoc = { ...doc(), packet: [{ id: 'Ether', fields: [{ id: 'type', value: 0x800 }] }, { id: 'Raw', fields: [{ id: 'load', value: { vtype: 'BYTES', base64: 'AAAA' } }] }] }
    vi.mocked(api.reconstructEdit).mockResolvedValue({ doc: fallen, warnings: ['re-derived'] })
    const onDocChange = vi.fn()
    render(<Host onDocChange={onDocChange} />)
    await typeByte(22, '3c')
    await waitFor(() => expect(onDocChange).toHaveBeenCalledWith(fallen))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Undo' })).toBeEnabled())
    await waitFor(() => expect(api.buildPacket).toHaveBeenLastCalledWith(fallen))
    await userEvent.keyboard('{Control>}z{/Control}')   // focus is on the hex byte, inside the editor
    await waitFor(() => expect(onDocChange).toHaveBeenLastCalledWith(doc()))
    expect(screen.getByRole('button', { name: 'Undo' })).toBeDisabled()   // one step took it all the way back
  })

  it('a frame under 14 bytes: a first layer of Raw is shown with its bytes', async () => {
    const short = btoa('\x01\x02\x03\x04\x05')
    vi.mocked(api.buildPacket).mockResolvedValue({
      binary: short, field_engine: null, error: null,
      data: [{ id: 'Raw', offset: 0, real_id: 'Raw', valid_structure: true, fields: [{ id: 'load', value: { vtype: 'BYTES', base64: short }, hvalue: '01 02 03 04 05', offset: 0, length: 5 }] }],
    })
    render(<Host onDocChange={() => undefined} initial={() => ({ ...doc(), packet: [{ id: 'Raw', fields: [] }] })} />)
    await ready()
    expect(pane('Raw')).toBeInTheDocument()
    expect(await screen.findAllByRole('button', { name: /^byte \d+: / })).toHaveLength(5)
    expect(screen.queryByRole('alert')).toBeNull()
  })
})
