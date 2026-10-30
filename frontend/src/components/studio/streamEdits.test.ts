import { describe, expect, it } from 'vitest'
import type { StreamJson, StreamRow } from '../../api'
import { deleteDiff, duplicateDiff, pasteDiff, renameDiff, reorderDiff, toggleDiff } from './streamEdits'

const stream = (name: string): StreamJson => ({
  name, enabled: true, self_start: true, isg: 0, action_count: 0, flags: 0,
  mode: { type: 'continuous', rate: { type: 'pps', value: 1 } }, packet: { binary: 'AAAA', meta: '' }, vm: { instructions: [] }, flow_stats: { enabled: false },
})
const row = (index: number, name: string): StreamRow => ({ index, stream: stream(name), length: 60, summary: { proto: 'UDP', info: '' }, has_editor: false })

describe('streamEdits', () => {
  it('reorder moves a block and stops at the edges', () => {
    expect(reorderDiff(5, [1, 2], -1, 'r')).toEqual({ rev: 'r', order: [1, 2, 0, 3, 4] })
    expect(reorderDiff(5, [1, 2], 1, 'r')).toEqual({ rev: 'r', order: [0, 3, 1, 2, 4] })
    expect(reorderDiff(5, [1, 3], 1, 'r')).toEqual({ rev: 'r', order: [0, 2, 1, 4, 3] })   // each moves one place
    expect(reorderDiff(5, [0, 1], -1, 'r')).toBeNull()
    expect(reorderDiff(5, [4], 1, 'r')).toBeNull()
    expect(reorderDiff(5, [], 1, 'r')).toBeNull()
  })

  it('paste after the selection builds +k tokens', () => {
    const a = stream('a'), b = stream('b')
    const d = pasteDiff(4, 1, [a, b], 'r')
    expect(d).toEqual({ rev: 'r', added: [a, b], order: [0, 1, '+0', '+1', 2, 3] })
    expect(d.added?.[0]).not.toBe(a)                                                  // copies, not the caller's objects
    expect(pasteDiff(4, null, [a], 'r')).toEqual({ rev: 'r', added: [a] })            // no order: appended
    expect(duplicateDiff([row(0, 'x'), row(1, 'y'), row(2, 'z')], [2, 0], 3, 'r'))
      .toEqual({ rev: 'r', added: [stream('x'), stream('z')], order: [0, 1, 2, '+0', '+1'] })
    expect(duplicateDiff([row(0, 'x')], [], 1, 'r')).toBeNull()
  })

  it('delete lists the selected old indices', () => {
    expect(deleteDiff([3, 0, 3, 1], 'r')).toEqual({ rev: 'r', removed: [0, 1, 3] })
  })

  it('toggle and rename send one-field patches', () => {
    expect(toggleDiff(7, false, 'r')).toEqual({ rev: 'r', changed: { 7: { enabled: false } } })
    expect(renameDiff(2, 'new', 'r')).toEqual({ rev: 'r', changed: { 2: { name: 'new' } } })
    expect(renameDiff(2, null, 'r')).toEqual({ rev: 'r', changed: { 2: { name: null } } })
  })
})
