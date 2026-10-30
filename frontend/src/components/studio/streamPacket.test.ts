import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import type { PacketDoc, StreamJson, StreamPage } from '../../api'
import { newDoc } from '../packet/trpFile'
import streams from '../../__fixtures__/studioStreams.json'
import { docForStream, macEdits, NOTE_TOO_LARGE, saveDiff, tooLarge } from './streamPacket'

afterEach(() => { vi.restoreAllMocks() })
const base = (streams.udp_for_benchmarks as unknown as StreamPage).rows[0]
const row = (over: Partial<typeof base> = {}) => ({ ...base, ...over })
const stored: PacketDoc = { ...newDoc(), metadata: { caption: 'stored' } }
const derived: PacketDoc = { ...newDoc(), metadata: { caption: 'derived' } }
const sidecar = (editor: unknown) => vi.spyOn(api, 'streamSidecar').mockResolvedValue({ rev: 'r', entry: { editor } as api.SidecarEntry })

describe('docForStream', () => {
  it('a matching sidecar doc is used', async () => {
    const side = sidecar({ doc: stored, binary: base.stream.packet.binary, vm: { instructions: [] } })
    const fromBytes = vi.spyOn(api, 'docFromBinary')
    const r = await docForStream('s', row({ has_editor: true }))
    expect(side).toHaveBeenCalledWith('s', 0)
    expect(r).toEqual({ doc: stored, fromSidecar: true, warnings: [] })
    expect(fromBytes).not.toHaveBeenCalled()
  })

  it('a stream whose packet changed elsewhere opens from its bytes', async () => {
    sidecar({ doc: stored, binary: 'AAAA', vm: { instructions: [] } })
    const fromBytes = vi.spyOn(api, 'docFromBinary').mockResolvedValue({ doc: derived, warnings: ['w'] })
    const r = await docForStream('s', row({ has_editor: true }))
    expect(fromBytes).toHaveBeenCalledWith(base.stream.packet.binary, { instructions: [] })
    expect(r).toEqual({ doc: derived, fromSidecar: false, warnings: ['w'] })
  })

  it('a changed vm also invalidates the doc', async () => {
    sidecar({ doc: stored, binary: base.stream.packet.binary, vm: { instructions: [{ type: 'fix_checksum_ipv4' }] } })
    const fromBytes = vi.spyOn(api, 'docFromBinary').mockResolvedValue({ doc: derived, warnings: [] })
    const r = await docForStream('s', row({ has_editor: true }))
    expect(r.doc).toBe(derived)
    expect(fromBytes).toHaveBeenCalledTimes(1)
  })

  it('a vm that differs only in key order still matches', async () => {
    const vm = { instructions: [{ type: 'flow_var', name: 'a', op: 'inc' }] }
    sidecar({ doc: stored, binary: base.stream.packet.binary, vm: { instructions: [{ op: 'inc', name: 'a', type: 'flow_var' }] } })
    const r = await docForStream('s', row({ has_editor: true, stream: { ...base.stream, vm } }))
    expect(r.fromSidecar).toBe(true)
  })

  it('a stream without has_editor fetches no sidecar entry', async () => {
    const side = vi.spyOn(api, 'streamSidecar')
    vi.spyOn(api, 'docFromBinary').mockResolvedValue({ doc: derived, warnings: [] })
    const r = await docForStream('s', row({ has_editor: false }))
    expect(side).not.toHaveBeenCalled()
    expect(r.fromSidecar).toBe(false)
  })

  it('an entry that cannot be read opens from the bytes and says so', async () => {
    vi.spyOn(api, 'streamSidecar').mockRejectedValue(new Error('down'))
    vi.spyOn(api, 'docFromBinary').mockResolvedValue({ doc: derived, warnings: ['w'] })
    const r = await docForStream('s', row({ has_editor: true }))
    expect(r.doc).toBe(derived)
    expect(r.warnings).toEqual([expect.stringContaining('could not be read'), 'w'])
  })
})

describe('saveDiff', () => {
  const stream: StreamJson = { ...base.stream, flags: 0, packet: { binary: 'OLD=', meta: 'keep me' } }
  const built = { packet: { binary: 'TkVX', meta: '' }, vm: { instructions: [{ type: 'fix_checksum_ipv4' }] } }
  const none = { src: false, dst: false }
  const diff = (mac = none, preset: number | null = null, doc: PacketDoc = stored, s: StreamJson = stream) => saveDiff(2, s, doc, built, mac, preset, 'r9')
  const patch = (d: ReturnType<typeof diff>) => (d.changed as Record<string, api.StreamPatch>)['2']

  it('save diff carries packet with its meta, vm and the sidecar entry', () => {
    const d = diff()
    expect(d.rev).toBe('r9')
    expect(patch(d)).toEqual({ packet: { binary: 'TkVX', meta: 'keep me' }, vm: built.vm })
    expect(d.sidecar).toEqual({ 2: { editor: { doc: stored, binary: 'TkVX', vm: built.vm } } })
    expect(tooLarge(d, 2)).toBeNull()
  })

  it('an oversized doc saves an empty entry with the note', () => {
    const big: PacketDoc = { ...stored, metadata: { caption: 'x'.repeat(api.SIDECAR_ENTRY_MAX) } }
    const d = diff(none, null, big)
    expect(d.sidecar).toEqual({ 2: {} })
    expect(tooLarge(d, 2)).toBe(NOTE_TOO_LARGE)
    expect(NOTE_TOO_LARGE).toBe('the editor document is too large to keep; the stream reopens from its bytes')
  })

  it('an Ether.src edit sets flags bit 0', () => {
    expect(patch(diff({ src: true, dst: false })).flags).toBe(1)
  })

  it('an Ether.dst edit sets the destination mode to 1, keeping the other bits', () => {
    expect(patch(diff({ src: false, dst: true })).flags).toBe(2)
    expect(patch(diff({ src: false, dst: true }, null, stored, { ...stream, flags: 4 | 8 })).flags).toBe(2 | 8)   // ARP mode (2) becomes packet mode (1); the dummy bit stays
    expect(patch(diff({ src: true, dst: true })).flags).toBe(3)
  })

  it('preset flags win over MAC edits', () => {
    expect(patch(diff({ src: true, dst: true }, 6)).flags).toBe(6)
    expect(patch(diff(none, 0)).flags).toBe(0)
  })

  it('no MAC edit leaves flags out', () => {
    expect('flags' in patch(diff())).toBe(false)
  })

  it('a stream without meta saves without one', () => {
    const d = diff(none, null, stored, { ...stream, packet: { binary: 'OLD=' } })
    expect(patch(d).packet).toEqual({ binary: 'TkVX' })
  })
})

describe('macEdits', () => {
  const ether = (fields: { id: string; value: string }[]): PacketDoc => ({ ...newDoc(), packet: [{ id: 'Ether', fields }, { id: 'IP', fields: [] }] })
  it('names the addresses that differ in the first Ether layer', () => {
    const a = ether([{ id: 'src', value: '00:00:00:00:00:01' }])
    expect(macEdits(a, a)).toEqual({ src: false, dst: false })
    expect(macEdits(a, ether([{ id: 'src', value: '00:00:00:00:00:02' }, { id: 'dst', value: '00:00:00:00:00:03' }]))).toEqual({ src: true, dst: true })
    expect(macEdits(a, ether([]))).toEqual({ src: true, dst: false })
  })
})
