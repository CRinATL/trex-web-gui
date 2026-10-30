import { describe, expect, it } from 'vitest'
import { HISTORY_CAP, History } from './undo'

describe('History', () => {
  it('cap drops the oldest', () => {
    const h = new History<number>(3)
    for (let i = 1; i <= 5; i++) h.push(i)       // 1 and 2 are gone
    const seen: (number | undefined)[] = []
    let cur = 6
    for (let i = 0; i < 4; i++) { const prev = h.undo(cur); seen.push(prev); if (prev !== undefined) cur = prev }
    expect(seen).toEqual([5, 4, 3, undefined])
    expect(new History<number>().undo(0)).toBeUndefined()
    expect(HISTORY_CAP).toBe(200)
  })

  it('the default cap is 200', () => {
    const h = new History<number>()
    for (let i = 0; i < 250; i++) h.push(i)
    let n = 0
    let cur = 250
    for (let prev = h.undo(cur); prev !== undefined; prev = h.undo(cur)) { cur = prev; n++ }
    expect([n, cur]).toEqual([200, 50])
  })

  it('push after undo clears redo', () => {
    const h = new History<string>()
    h.push('a'); h.push('b')
    expect(h.undo('c')).toBe('b')
    expect(h.canRedo).toBe(true)
    h.push('b')                                   // a new change from b
    expect(h.canRedo).toBe(false)
    expect(h.redo('x')).toBeUndefined()
  })

  it('redo returns what undo left, and undo goes back again', () => {
    const h = new History<string>()
    h.push('a'); h.push('b')
    expect(h.undo('c')).toBe('b')
    expect(h.undo('b')).toBe('a')
    expect(h.redo('a')).toBe('b')
    expect(h.redo('b')).toBe('c')
    expect(h.redo('c')).toBeUndefined()
    expect([h.canUndo, h.canRedo]).toEqual([true, false])
  })

  it('drop takes back the newest push without a redo, and only when it is the newest', () => {
    const a = { n: 1 }, b = { n: 2 }
    const h = new History<{ n: number }>()
    h.push(a); h.push(b)
    expect(h.drop(a)).toBe(false)
    expect(h.drop(b)).toBe(true)
    expect([h.canUndo, h.canRedo]).toEqual([true, false])
  })
})
