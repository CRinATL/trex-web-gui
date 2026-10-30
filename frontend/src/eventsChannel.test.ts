import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { publishLog, useChannelLog } from './eventsChannel'
import type { LogEntry } from './state'

// A same-process stand-in for BroadcastChannel: a post reaches every other instance of the same name.
function makeFake() {
  return class FakeChannel {
    static all: FakeChannel[] = []
    onmessage: ((e: { data: unknown }) => void) | null = null
    posted: unknown[] = []
    closed = false
    name: string
    constructor(name: string) { this.name = name; FakeChannel.all.push(this) }
    postMessage(data: unknown) {
      this.posted.push(data)
      for (const c of FakeChannel.all) if (c !== this && !c.closed && c.name === this.name) c.onmessage?.({ data })
    }
    close() { this.closed = true }
  }
}
let Fake = makeFake()

const e = (message: string): LogEntry => ({ kind: 'INFO', ts: 1, message })
beforeEach(() => { Fake = makeFake(); vi.stubGlobal('BroadcastChannel', Fake) })
afterEach(() => { vi.unstubAllGlobals() })

describe('eventsChannel', () => {
  it('is a no-op without BroadcastChannel', () => {
    vi.stubGlobal('BroadcastChannel', undefined)
    expect(() => publishLog([e('a')])).not.toThrow()
    const { result } = renderHook(() => useChannelLog())
    expect(result.current).toBeNull()
  })
  it('the pop-out stays null until a log message, says hello, and follows new publishes', () => {
    publishLog([e('a')])   // the main window publishes before any pop-out exists
    const { result } = renderHook(() => useChannelLog())
    // the hello reached the main window's channel, which answers with the log it holds
    expect(result.current).toEqual([e('a')])
    act(() => publishLog([e('a'), e('b')]))
    expect(result.current).toEqual([e('a'), e('b')])
  })
  it('stays null while no main window has published', () => {
    const { result } = renderHook(() => useChannelLog())
    expect(result.current).toBeNull()
    expect(Fake.all.some((c) => c.posted.some((m) => (m as { type: string }).type === 'hello'))).toBe(true)
  })
})
