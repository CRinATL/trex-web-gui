import { beforeEach, describe, expect, it } from 'vitest'
import type { CapturePktsMsg, DecodedRow } from './api'
import { CAPACITY, captureStore } from './captureStore'
import capturePkts from './__fixtures__/capturePkts.json'

const row = (index: number): DecodedRow => ({ ...(capturePkts.batch.rows[0] as DecodedRow), index })
const batch = (from: number, n: number, monitorId = 2): CapturePktsMsg =>
  ({ type: 'capture_pkts', monitor_id: monitorId, rows: Array.from({ length: n }, (_, i) => row(from + i)) })

beforeEach(() => captureStore.clear())

describe('captureStore', () => {
  it('ring keeps the last 5000 rows and counts the dropped', () => {
    expect(CAPACITY).toBe(5000)
    captureStore.push(batch(1, 4990))
    expect(captureStore.droppedByBrowser()).toBe(0)
    captureStore.push(batch(4991, 30))
    const rows = captureStore.rows()
    expect(rows).toHaveLength(5000)
    expect(rows[0].index).toBe(21)
    expect(rows[4999].index).toBe(5020)
    expect(captureStore.droppedByBrowser()).toBe(20)
  })

  it('a single batch larger than the ring counts the rows it cannot hold', () => {
    captureStore.push(batch(1, 5003))
    expect(captureStore.rows()).toHaveLength(5000)
    expect(captureStore.rows()[0].index).toBe(4)
    expect(captureStore.droppedByBrowser()).toBe(3)
  })

  it('clear empties and resets the count, and tells subscribers', () => {
    captureStore.push(batch(1, 5001))
    let calls = 0
    const off = captureStore.subscribe(() => { calls++ })
    captureStore.clear()
    off()
    expect(calls).toBe(1)
    expect(captureStore.rows()).toEqual([])
    expect(captureStore.droppedByBrowser()).toBe(0)
  })

  it('reset empties like clear', () => {
    captureStore.push(batch(1, 5))
    captureStore.reset()
    expect(captureStore.rows()).toEqual([])
  })

  it('a batch from another monitor replaces the old monitor\'s rows and its dropped count', () => {
    captureStore.push(batch(1, 5001, 1))
    expect(captureStore.droppedByBrowser()).toBe(1)
    captureStore.push(batch(1, 3, 2))        // a new monitor started elsewhere: its index restarts at 1
    expect(captureStore.rows().map((r) => r.index)).toEqual([1, 2, 3])
    expect(captureStore.droppedByBrowser()).toBe(0)
    captureStore.push(batch(4, 2, 2))        // the same monitor keeps appending
    expect(captureStore.rows().map((r) => r.index)).toEqual([1, 2, 3, 4, 5])
  })

  it('rows identity is stable without a push', () => {
    captureStore.push(batch(1, 3))
    const a = captureStore.rows()
    expect(captureStore.rows()).toBe(a)
    captureStore.push(batch(4, 1))
    expect(captureStore.rows()).not.toBe(a)
    expect(a).toHaveLength(3)   // the earlier snapshot is never mutated
  })
})
