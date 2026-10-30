import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import { ApiError, type DiffReply, type StreamsDiff } from '../../api'
import { renameDiff, toggleDiff } from './streamEdits'
import { ProfileWriter } from './studioWriter'

afterEach(() => vi.restoreAllMocks())
const ok = (rev: string): DiffReply => ({ rev, stream_count: 2, renamed: {}, cleared: [], warnings: [] })

// putStreams answers each call only when the test resolves or rejects it.
function held() {
  const calls: { diff: StreamsDiff; resolve: (r: DiffReply) => void; reject: (e: unknown) => void }[] = []
  const put = vi.spyOn(api, 'putStreams').mockImplementation((_slug, diff) =>
    new Promise<DiffReply>((resolve, reject) => { calls.push({ diff, resolve, reject }) }))
  return { calls, put }
}

describe('ProfileWriter', () => {
  it('two quick enabled toggles send the second with the first reply\'s rev', async () => {
    const { calls, put } = held()
    const w = new ProfileWriter('p', 'r0')
    const one = w.write((rev) => toggleDiff(0, false, rev))
    const two = w.write((rev) => toggleDiff(1, false, rev))
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    expect(calls[0].diff.rev).toBe('r0')
    calls[0].resolve(ok('r1'))
    expect(await one).toEqual(ok('r1'))
    await vi.waitFor(() => expect(calls).toHaveLength(2))
    expect(calls[1].diff).toEqual({ rev: 'r1', changed: { 1: { enabled: false } } })
    calls[1].resolve(ok('r2'))
    await two
    expect(w.rev).toBe('r2')
    expect(put).toHaveBeenCalledWith('p', expect.anything())
  })

  it('a toggle queued behind a rename keeps the new name', async () => {
    const { calls } = held()
    const w = new ProfileWriter('p', 'r0')
    const rename = w.write((rev) => renameDiff(0, 'renamed', rev))
    const toggle = w.write((rev) => toggleDiff(0, false, rev))
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    calls[0].resolve(ok('r1'))
    await rename
    await vi.waitFor(() => expect(calls).toHaveLength(2))
    expect(calls[1].diff.rev).toBe('r1')
    expect(calls[1].diff.changed?.['0']).toEqual({ enabled: false })
    expect(JSON.stringify(calls[1].diff)).not.toContain('name')
    calls[1].resolve(ok('r2'))
    await toggle
  })

  it('a stale reply rejects the queue and marks the writer stale', async () => {
    const { calls, put } = held()
    const w = new ProfileWriter('p', 'r0')
    const seen: string[] = []
    w.subscribe(() => seen.push(w.getState()))
    const one = w.write((rev) => toggleDiff(0, false, rev))
    const two = w.write((rev) => toggleDiff(1, false, rev))
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    const stale = new ApiError(409, 'stale', 'The profile changed', { rev: 'other' })
    calls[0].reject(stale)
    await expect(one).rejects.toBe(stale)
    await expect(two).rejects.toBe(stale)
    await expect(w.write((rev) => toggleDiff(0, true, rev))).rejects.toBe(stale)
    expect(put).toHaveBeenCalledTimes(1)
    expect(w.getState()).toBe('stale')
    expect(seen).toEqual(['stale'])
  })

  it('pending counts queued and in-flight writes, and its listeners hear every change but not state changes', async () => {
    const { calls } = held()
    const w = new ProfileWriter('p', 'r0')
    const seen: number[] = []
    w.subscribePending(() => seen.push(w.pending))
    expect(w.pending).toBe(0)
    const one = w.write((rev) => toggleDiff(0, false, rev))
    const two = w.write((rev) => toggleDiff(1, false, rev))
    expect(w.pending).toBe(2)
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    calls[0].resolve(ok('r1'))
    await one
    expect(w.pending).toBe(1)
    await vi.waitFor(() => expect(calls).toHaveLength(2))
    calls[1].reject(new Error('refused'))
    await expect(two).rejects.toThrow('refused')
    expect(w.pending).toBe(0)
    expect(seen).toEqual([1, 2, 1, 0])
  })

  it('a 404 marks the writer gone', async () => {
    const { calls } = held()
    const w = new ProfileWriter('p', 'r0')
    const one = w.write((rev) => toggleDiff(0, false, rev))
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    const gone = new ApiError(404, 'not_found', 'No profile p')
    calls[0].reject(gone)
    await expect(one).rejects.toBe(gone)
    expect(w.getState()).toBe('gone')
    await expect(w.write((rev) => toggleDiff(0, true, rev))).rejects.toBe(gone)
  })

  it('another refusal rejects only its own write', async () => {
    const { calls } = held()
    const w = new ProfileWriter('p', 'r0')
    const bad = new ApiError(422, 'invalid_stream', 'stream 0 name: taken', { index: 0, field: 'name' })
    const one = w.write((rev) => renameDiff(0, 'b', rev))
    const two = w.write((rev) => toggleDiff(1, false, rev))
    await vi.waitFor(() => expect(calls).toHaveLength(1))
    calls[0].reject(bad)
    await expect(one).rejects.toBe(bad)
    await vi.waitFor(() => expect(calls).toHaveLength(2))
    expect(calls[1].diff.rev).toBe('r0')
    calls[1].resolve(ok('r1'))
    await two
    expect(w.getState()).toBe('ready')
  })
})
