import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ApiError, type BuildResult, type PacketDoc } from '../../api'
import { appendLayer, setField } from './packetModel'
import { RESTART_TEXT, useEditor } from './useEditor'

const base = (): PacketDoc => ({
  fileType: 'trex-packet-editor', version: '1.0.0', metadata: { caption: '' }, fePrarameters: {}, feInstructions: [],
  packet: [{ id: 'Ether', fields: [] }, { id: 'IP', fields: [] }, { id: 'UDP', fields: [] }],
})
// A build result that names the document it was made from.
const ok = (doc: PacketDoc): BuildResult => ({ binary: btoa(JSON.stringify(doc.packet)), data: [], field_engine: null, error: null })
const failed = (message: string): BuildResult => ({ binary: null, data: [], field_engine: null, error: message })
const ttl = (doc: PacketDoc) => doc.packet[1].fields.find((f) => f.id === 'ttl')?.value
const src = (doc: PacketDoc) => doc.packet[0].fields.find((f) => f.id === 'src')?.value

// A build whose calls the test settles by hand.
function manual() {
  const calls: { doc: PacketDoc; resolve: (r: BuildResult) => void; reject: (e: unknown) => void }[] = []
  const build = vi.fn((doc: PacketDoc) => new Promise<BuildResult>((resolve, reject) => { calls.push({ doc, resolve, reject }) }))
  return { calls, build }
}
const restarted = () => new ApiError(503, 'engine_restarted', 'The packet engine restarted')

describe('useEditor', () => {
  it('builds the initial document and keeps the result', async () => {
    const build = vi.fn(async (d: PacketDoc) => ok(d))
    const { result } = renderHook(() => useEditor(base(), { build }))
    await waitFor(() => expect(result.current.lastGood).not.toBeNull())
    expect(result.current.result).toBe(result.current.lastGood)
    expect([result.current.error, result.current.pending, result.current.canUndo]).toEqual([null, false, false])
    expect(build).toHaveBeenCalledTimes(1)
  })

  it('a failed build keeps the last good result and marks the field', async () => {
    const build = vi.fn(async (d: PacketDoc) => (ttl(d) === 'abc' ? failed('ValueError: bad ttl') : ok(d)))
    const { result } = renderHook(() => useEditor(base(), { build }))
    await waitFor(() => expect(result.current.lastGood).not.toBeNull())
    const good = result.current.lastGood
    act(() => result.current.apply((d) => setField(d, 1, 'ttl', 'abc'), 'set ttl'))
    await waitFor(() => expect(result.current.error).not.toBeNull())
    expect(result.current.error).toMatchObject({ kind: 'build', message: 'ValueError: bad ttl', layer: 1, field: 'ttl' })
    expect(result.current.lastGood).toBe(good)                       // the hex and the tree keep the last good build
    expect(result.current.result?.error).toBe('ValueError: bad ttl')
    expect(ttl(result.current.doc)).toBe('abc')                      // the user's text stays
    // a later good value clears it
    act(() => result.current.apply((d) => setField(d, 1, 'ttl', 64), 'set ttl'))
    await waitFor(() => expect(result.current.error).toBeNull())
    expect(result.current.lastGood).not.toBe(good)
  })

  it('a failure elsewhere keeps the mark on the field that is still bad', async () => {
    const build = vi.fn(async (d: PacketDoc) => (ttl(d) === 'abc' ? failed('ValueError: bad ttl') : ok(d)))
    const { result } = renderHook(() => useEditor(base(), { build }))
    await waitFor(() => expect(result.current.lastGood).not.toBeNull())
    act(() => result.current.apply((d) => setField(d, 1, 'ttl', 'abc'), 'set ttl'))
    await waitFor(() => expect(result.current.error?.field).toBe('ttl'))
    act(() => result.current.apply((d) => setField(d, 0, 'src', '00:11:22:33:44:55'), 'set src'))   // still fails, because of ttl
    await waitFor(() => expect(build).toHaveBeenCalledTimes(3))
    await waitFor(() => expect(result.current.pending).toBe(false))
    expect(result.current.error).toMatchObject({ layer: 1, field: 'ttl' })
  })

  it('a failed append undoes itself', async () => {
    const build = vi.fn(async (d: PacketDoc) => (d.packet.some((l) => l.id === 'Bogus') ? failed('Error: no such protocol') : ok(d)))
    const { result } = renderHook(() => useEditor(base(), { build }))
    await waitFor(() => expect(result.current.lastGood).not.toBeNull())
    const good = result.current.lastGood
    act(() => result.current.apply((d) => appendLayer(d, 'Bogus'), 'append Bogus'))
    expect(result.current.doc.packet).toHaveLength(4)                // shown at once
    await waitFor(() => expect(result.current.doc.packet).toHaveLength(3))
    expect(result.current.error).toMatchObject({ kind: 'append', message: 'append Bogus was undone: Error: no such protocol' })
    expect(result.current.lastGood).toBe(good)
    expect(result.current.result).toBe(good)
    expect([result.current.canUndo, result.current.canRedo]).toEqual([false, false])   // the append left no history
  })

  it('a failed append that is not the latest edit is not undone', async () => {
    const m = manual()
    const { result } = renderHook(() => useEditor(base(), { build: m.build }))
    await waitFor(() => expect(m.calls).toHaveLength(1))
    act(() => m.calls[0].resolve(ok(m.calls[0].doc)))
    await waitFor(() => expect(result.current.lastGood).not.toBeNull())
    act(() => result.current.apply((d) => appendLayer(d, 'Bogus'), 'append Bogus'))
    await waitFor(() => expect(m.calls).toHaveLength(2))
    act(() => result.current.apply((d) => setField(d, 1, 'ttl', 5), 'set ttl'))       // a newer edit while the append builds
    act(() => m.calls[1].resolve(failed('Error: no such protocol')))                  // the append's reply is stale
    await waitFor(() => expect(m.calls).toHaveLength(3))
    expect(result.current.doc.packet).toHaveLength(4)
    expect(result.current.error).toBeNull()
    expect(result.current.canUndo).toBe(true)
  })

  it('a restart caused by this editor\'s commit is not re-sent', async () => {
    const build = vi.fn(async (d: PacketDoc) => { if (ttl(d) === 99) throw restarted(); return ok(d) })
    const { result } = renderHook(() => useEditor(base(), { build }))
    await waitFor(() => expect(result.current.lastGood).not.toBeNull())
    const good = result.current.lastGood
    act(() => result.current.apply((d) => setField(d, 1, 'ttl', 99), 'set ttl'))
    await waitFor(() => expect(result.current.error).not.toBeNull())
    expect(result.current.error).toMatchObject({ kind: 'restart', message: RESTART_TEXT, layer: 1, field: 'ttl' })
    expect(RESTART_TEXT).toBe('the packet engine restarted while evaluating this value')
    expect(result.current.lastGood).toBe(good)
    await new Promise((r) => setTimeout(r, 30))
    expect(build).toHaveBeenCalledTimes(2)                           // the initial build and the one that killed the worker; no third
    expect(ttl(result.current.doc)).toBe(99)
    act(() => result.current.apply((d) => setField(d, 1, 'ttl', 64), 'set ttl'))      // the next edit works
    await waitFor(() => expect(result.current.error).toBeNull())
    expect(build).toHaveBeenCalledTimes(3)
  })

  it('a restart on a superseded reply rebuilds once', async () => {
    const m = manual()
    const { result } = renderHook(() => useEditor(base(), { build: m.build }))
    await waitFor(() => expect(m.calls).toHaveLength(1))
    act(() => m.calls[0].resolve(ok(m.calls[0].doc)))
    await waitFor(() => expect(result.current.lastGood).not.toBeNull())
    act(() => result.current.apply((d) => setField(d, 1, 'ttl', 1), 'set ttl'))       // call 1: in flight
    await waitFor(() => expect(m.calls).toHaveLength(2))
    act(() => result.current.apply((d) => setField(d, 1, 'ttl', 2), 'set ttl'))       // newer, waiting
    act(() => m.calls[1].reject(restarted()))                                          // the older request kills the worker
    await waitFor(() => expect(m.calls).toHaveLength(3))
    expect(ttl(m.calls[2].doc)).toBe(2)                                                // the current document is built, once
    act(() => m.calls[2].resolve(ok(m.calls[2].doc)))
    await waitFor(() => expect(result.current.pending).toBe(false))
    expect(result.current.error).toBeNull()                                            // the superseded restart is not shown
    expect(result.current.lastGood?.binary).toBe(ok(m.calls[2].doc).binary)
    expect(m.build).toHaveBeenCalledTimes(3)
  })

  it('an older reply never replaces a newer one, and pending covers the wait', async () => {
    const m = manual()
    const { result } = renderHook(() => useEditor(base(), { build: m.build }))
    await waitFor(() => expect(m.calls).toHaveLength(1))
    expect(result.current.pending).toBe(true)
    act(() => result.current.apply((d) => setField(d, 1, 'ttl', 1), 'set ttl'))
    act(() => m.calls[0].resolve(ok(m.calls[0].doc)))                                  // the initial reply is stale now
    await waitFor(() => expect(m.calls).toHaveLength(2))
    expect(result.current.lastGood).toBeNull()
    act(() => m.calls[1].resolve(ok(m.calls[1].doc)))
    await waitFor(() => expect(result.current.pending).toBe(false))
    expect(result.current.lastGood?.binary).toBe(ok(m.calls[1].doc).binary)
  })

  it('should_do_undo_redo', async () => {
    const build = vi.fn(async (d: PacketDoc) => ok(d))
    const { result } = renderHook(() => useEditor(base(), { build }))
    await waitFor(() => expect(result.current.lastGood).not.toBeNull())
    const settle = () => waitFor(() => expect(result.current.pending).toBe(false))
    const set = async (v: string) => { act(() => result.current.apply((d) => setField(d, 0, 'src', v), 'set src')); await settle() }
    const step = async (fn: () => void) => { act(fn); await settle() }

    await set('00:11:22:33:44:55')
    expect(src(result.current.doc)).toBe('00:11:22:33:44:55')
    await set('00:11:22:33:44:99')
    await step(result.current.undo)
    expect(src(result.current.doc)).toBe('00:11:22:33:44:55')
    await step(result.current.undo)                                  // undo all changes
    expect(src(result.current.doc)).toBeUndefined()
    await step(result.current.redo)
    expect(src(result.current.doc)).toBe('00:11:22:33:44:55')
    await step(result.current.redo)
    expect(src(result.current.doc)).toBe('00:11:22:33:44:99')
    const calls = build.mock.calls.length
    await step(result.current.redo)                                  // the extra redo does nothing
    expect(src(result.current.doc)).toBe('00:11:22:33:44:99')
    expect(build.mock.calls.length).toBe(calls)
    await step(result.current.undo)
    await step(result.current.undo)                                  // back to the initial state
    await step(result.current.undo)                                  // the extra undo does nothing
    expect(src(result.current.doc)).toBeUndefined()
    expect([result.current.canUndo, result.current.canRedo]).toEqual([false, true])
  })

  it('reset replaces the document, clears the history and the old result', async () => {
    const build = vi.fn(async (d: PacketDoc) => ok(d))
    const { result } = renderHook(() => useEditor(base(), { build }))
    await waitFor(() => expect(result.current.lastGood).not.toBeNull())
    act(() => result.current.apply((d) => setField(d, 1, 'ttl', 5), 'set ttl'))
    await waitFor(() => expect(result.current.pending).toBe(false))
    const fresh = { ...base(), metadata: { caption: 'new' } }
    act(() => result.current.reset(fresh))
    expect(result.current.doc).toBe(fresh)
    expect([result.current.canUndo, result.current.lastGood]).toEqual([false, null])
    await waitFor(() => expect(result.current.lastGood).not.toBeNull())
    expect(build).toHaveBeenLastCalledWith(fresh)
  })

  it('a field engine error keeps the packet result and is reported on the instruction', async () => {
    const build = vi.fn(async (d: PacketDoc): Promise<BuildResult> => (d.feInstructions.length > 0
      ? { ...ok(d), field_engine: { instructions: {}, error: 'no field IP.nope' } } : ok(d)))
    const { result } = renderHook(() => useEditor(base(), { build }))
    await waitFor(() => expect(result.current.lastGood).not.toBeNull())
    act(() => result.current.apply((d) => ({ ...d, feInstructions: [{ id: 'STLVmWrFlowVar', parameters: {} }] }), 'add instruction'))
    await waitFor(() => expect(result.current.error).not.toBeNull())
    expect(result.current.error).toMatchObject({ kind: 'fe', message: 'no field IP.nope', instruction: 0 })
    expect(result.current.lastGood?.binary).toBe(ok(result.current.doc).binary)
  })
})
