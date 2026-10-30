import { describe, expect, it } from 'vitest'
import type { StreamJson, StreamRow } from '../../api'
import { duplicateDiff, pasteDiff } from './streamEdits'
import { ENTRY_BUDGET, HISTORY_MAX, NOT_RESTORED, UndoHistory, undoAdd, undoDelete, undoPatch, undoReorder, undoRename, undoToggle } from './streamUndo'

const stream = (name: string, next?: string): StreamJson => ({
  name, ...(next ? { next } : {}), enabled: true, self_start: true, isg: 0, action_count: 0, flags: 0,
  mode: { type: 'continuous', rate: { type: 'pps', value: 1 } }, packet: { binary: 'AAAA', meta: '' }, vm: { instructions: [] }, flow_stats: { enabled: false },
})
const row = (index: number, s: StreamJson, has_editor = false): StreamRow => ({ index, stream: s, length: 60, summary: { proto: 'UDP', info: '' }, has_editor })
const entry = (caption: string, pad = 0) => ({ editor: { doc: { metadata: { caption: caption + 'z'.repeat(pad) } }, binary: 'AAAA', vm: { instructions: [] } } })

describe('streamUndo', () => {
  it('undo of a delete re-adds at the old index with its sidecar and restores cleared next', () => {
    // streams a b c d: b and d were deleted; a's next named b and was cleared; 2 remain (a, c)
    const rows = [row(1, stream('b'), true), row(3, stream('d'))]
    const e = undoDelete(rows, { 1: entry('b') }, { 0: 'b' }, 2)
    expect(e.shifts).toBe(true)
    expect(e.label).toBe('deleting 2 streams')
    expect(e.undo('r7')).toEqual({
      rev: 'r7', added: [stream('b'), stream('d')], order: [0, '+0', 1, '+1'], added_sidecar: [entry('b'), {}],
      changed: { 0: { next: 'b' } },
    })
    expect(e.redo('r8')).toEqual({ rev: 'r8', removed: [1, 3] })
  })

  it('undo of a delete maps a cleared next to its index after the delete', () => {
    // streams a b c: a deleted; c's next named a and was cleared; c is index 1 afterwards
    const e = undoDelete([row(0, stream('a'))], {}, { 2: 'a' }, 2)
    expect(e.undo('r').changed).toEqual({ 1: { next: 'a' } })
    expect(e.undo('r').order).toEqual(['+0', 0, 1])
  })

  it('undo of a delete past the 8 MiB budget says the documents are not restored', () => {
    const big = entry('x', ENTRY_BUDGET / 2 + 10)
    const rows = [row(0, stream('a'), true), row(1, stream('b'), true), row(2, stream('c'), true)]
    const over = undoDelete(rows, { 0: big, 1: big, 2: big }, {}, 0)
    expect(over.label).toBe(`deleting 3 streams${NOT_RESTORED}`)
    expect(over.undo('r').added_sidecar).toBeUndefined()
    expect(over.undo('r').added).toHaveLength(3)
    // a row whose document was not fetched says so too; within budget the entries are carried
    const missing = undoDelete(rows.slice(0, 1), {}, {}, 2)
    expect(missing.label).toBe(`deleting a stream${NOT_RESTORED}`)
    const fine = undoDelete(rows.slice(0, 1), { 0: big }, {}, 2)
    expect(fine.label).toBe('deleting a stream')
    expect(fine.undo('r').added_sidecar).toEqual([big])
  })

  it('undo of a rename restores the name and every next', () => {
    // b renamed to nothing: a and c, which went to b, were cleared
    const gone = undoRename(1, 'b', null, { 0: 'b', 2: 'b' })
    expect(gone.undo('r')).toEqual({ rev: 'r', changed: { 1: { name: 'b' }, 0: { next: 'b' }, 2: { next: 'b' } } })
    expect(gone.redo('r')).toEqual({ rev: 'r', changed: { 1: { name: null } } })
    // to another name: the backend follows the rename, so the name alone puts every next back
    const other = undoRename(1, 'b', 'bb')
    expect(other.undo('r')).toEqual({ rev: 'r', changed: { 1: { name: 'b' } } })
    expect(other.redo('r')).toEqual({ rev: 'r', changed: { 1: { name: 'bb' } } })
    expect(undoRename(0, null, 'x').undo('r')).toEqual({ rev: 'r', changed: { 0: { name: null } } })
  })

  it('undo of a paste removes exactly the added rows', () => {
    const added = [stream('x'), stream('y')]
    const mid = undoAdd(4, added, 2, [entry('x'), {}])
    expect(mid.shifts).toBe(true)
    expect(mid.undo('r')).toEqual({ rev: 'r', removed: [2, 3] })
    expect(mid.redo('r2')).toEqual(pasteDiff(4, 1, added, 'r2', [entry('x'), {}]))
    const end = undoAdd(4, [stream('z')])
    expect(end.undo('r')).toEqual({ rev: 'r', removed: [4] })
    expect(end.redo('r')).toEqual({ rev: 'r', added: [stream('z')] })
    // a duplicate undoes the same way, and its entries come back with the redo
    const dup = duplicateDiff([row(0, stream('a'), true)], [0], 3, 'r', { 0: entry('a') })
    expect(dup?.added_sidecar).toEqual([entry('a')])
  })

  it('undo of a reorder applies the inverse permutation', () => {
    const e = undoReorder([2, 0, 1, 3])
    expect(e.undo('r')).toEqual({ rev: 'r', order: [1, 2, 0, 3] })
    expect(e.redo('r')).toEqual({ rev: 'r', order: [2, 0, 1, 3] })
    // applying the order, then the inverse, gives the identity
    const apply = (items: number[], order: number[]) => order.map((i) => items[i])
    expect(apply(apply([0, 1, 2, 3], [2, 0, 1, 3]), e.undo('r').order as number[])).toEqual([0, 1, 2, 3])
  })

  it('undo of an embedded Save restores the old sidecar entry', () => {
    const before = stream('a')
    const patch = { packet: { binary: 'BBBB', meta: '' }, vm: { instructions: [{ type: 'var' }] } }
    const e = undoPatch(0, before, patch, entry('old'), { afterSidecar: entry('new') })
    expect(e.undo('r')).toEqual({ rev: 'r', changed: { 0: { packet: before.packet, vm: before.vm } }, sidecar: { 0: entry('old') } })
    expect(e.redo('r')).toEqual({ rev: 'r', changed: { 0: patch }, sidecar: { 0: entry('new') } })
    // a Save with no entry on either side sends no sidecar key; a key the stream lacked goes back to its default
    const plain = undoPatch(0, before, { name: 'q', core_id: 3, random_seed: 5 })
    expect(plain.undo('r')).toEqual({ rev: 'r', changed: { 0: { name: 'a', core_id: -1, random_seed: null } } })
    expect(plain.redo('r')).not.toHaveProperty('sidecar')
    // an empty old entry is still written back (it clears the new one)
    expect(undoPatch(0, before, patch, {}).undo('r').sidecar).toEqual({ 0: {} })
  })

  it('redo rebuilds the original diff with the new rev', () => {
    const t = undoToggle(2, true)
    expect(t.undo('r5')).toEqual({ rev: 'r5', changed: { 2: { enabled: true } } })
    expect(t.redo('r6')).toEqual({ rev: 'r6', changed: { 2: { enabled: false } } })
    expect(undoDelete([row(1, stream('b'))], {}, {}, 3).redo('r9')).toEqual({ rev: 'r9', removed: [1] })
  })

  it('the history keeps 100 entries and a new action empties the redo stack', () => {
    const h = new UndoHistory()
    for (let i = 0; i < HISTORY_MAX + 5; i++) h.push(undoToggle(i, true))
    expect(h.nextUndo()?.undo('r')).toEqual({ rev: 'r', changed: { [HISTORY_MAX + 4]: { enabled: true } } })
    h.undid()
    expect(h.nextRedo()).toBeDefined()
    h.push(undoToggle(0, true))
    expect(h.nextRedo()).toBeUndefined()
    for (let i = 0; i < HISTORY_MAX + 10; i++) h.undid()
    expect(h.nextUndo()).toBeUndefined()
    let n = 0
    while (h.nextRedo()) { h.redid(); n++ }
    expect(n).toBe(HISTORY_MAX)
    h.clear()
    expect(h.nextUndo()).toBeUndefined()
  })
})
