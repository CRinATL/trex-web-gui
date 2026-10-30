import { afterEach, describe, expect, it, vi } from 'vitest'
import { connectSocket, type SocketDeps } from './ws'

const deps = (authenticated = true): SocketDeps => ({ authStatus: () => Promise.resolve(authenticated), onUnauthenticated: () => undefined })

class FakeWS {
  static all: FakeWS[] = []
  onopen?: () => void; onclose?: () => void; onmessage?: (e: { data: string }) => void
  url: string
  constructor(url: string) { this.url = url; FakeWS.all.push(this) }
  close() { this.onclose?.() }
}

afterEach(() => { vi.useRealTimers(); FakeWS.all = [] })

describe('connectSocket', () => {
  it('ws reconnects after close with backoff and reports status', () => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', FakeWS)
    const status: boolean[] = []
    const msgs: unknown[] = []
    const stop = connectSocket('ws://x/api/ws', (m) => msgs.push(m), (o) => status.push(o), deps())
    FakeWS.all[0].onopen?.()
    FakeWS.all[0].onmessage?.({ data: '{"type":"hello"}' })
    FakeWS.all[0].onclose?.()
    expect(status).toEqual([true, false])
    vi.advanceTimersByTime(499); expect(FakeWS.all).toHaveLength(1)
    vi.advanceTimersByTime(1); expect(FakeWS.all).toHaveLength(2)
    FakeWS.all[1].onopen?.(); FakeWS.all[1].onclose?.(); vi.advanceTimersByTime(999); expect(FakeWS.all).toHaveLength(2)
    vi.advanceTimersByTime(1); expect(FakeWS.all).toHaveLength(3)
    stop(); FakeWS.all[2].onclose?.(); vi.advanceTimersByTime(10000)
    expect(FakeWS.all).toHaveLength(3)
    expect(msgs).toEqual([{ type: 'hello' }])
  })

  it('backoff resets on the first received message, not on open', () => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', FakeWS)
    connectSocket('ws://x/api/ws', () => undefined, () => undefined, deps())
    FakeWS.all[0].onopen?.(); FakeWS.all[0].onclose?.()   // accepted, silent, closed
    vi.advanceTimersByTime(500); expect(FakeWS.all).toHaveLength(2)
    FakeWS.all[1].onopen?.(); FakeWS.all[1].onclose?.()   // still no message: delay doubled to 1000
    vi.advanceTimersByTime(999); expect(FakeWS.all).toHaveLength(2)
    vi.advanceTimersByTime(1); expect(FakeWS.all).toHaveLength(3)
    FakeWS.all[2].onopen?.(); FakeWS.all[2].onmessage?.({ data: '{"type":"hello"}' }); FakeWS.all[2].onclose?.()
    vi.advanceTimersByTime(499); expect(FakeWS.all).toHaveLength(3)
    vi.advanceTimersByTime(1); expect(FakeWS.all).toHaveLength(4)
  })

  it('ignores a malformed frame', () => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', FakeWS)
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    const msgs: unknown[] = []
    connectSocket('ws://x/api/ws', (m) => msgs.push(m), () => undefined, deps())
    expect(() => FakeWS.all[0].onmessage?.({ data: '{not json' })).not.toThrow()
    FakeWS.all[0].onmessage?.({ data: '{"type":"hello"}' })
    expect(msgs).toEqual([{ type: 'hello' }])
    expect(warn).toHaveBeenCalledTimes(1)
    warn.mockRestore()
  })

  it('a 1006 close before open asks auth status and stops when unauthenticated', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', FakeWS)
    const onUnauthenticated = vi.fn()
    const authStatus = vi.fn(() => Promise.resolve(false))
    connectSocket('ws://x/api/ws', () => undefined, () => undefined, { authStatus, onUnauthenticated })
    FakeWS.all[0].onclose?.()                    // the handshake was refused: closed, never opened
    await vi.advanceTimersByTimeAsync(60000)
    expect(authStatus).toHaveBeenCalledTimes(1)
    expect(onUnauthenticated).toHaveBeenCalledTimes(1)
    expect(FakeWS.all).toHaveLength(1)
  })

  it('a 1006 close before open keeps reconnecting while authenticated', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', FakeWS)
    const onUnauthenticated = vi.fn()
    connectSocket('ws://x/api/ws', () => undefined, () => undefined, { authStatus: () => Promise.resolve(true), onUnauthenticated })
    FakeWS.all[0].onclose?.()
    await vi.advanceTimersByTimeAsync(500)
    expect(FakeWS.all).toHaveLength(2)
    expect(onUnauthenticated).not.toHaveBeenCalled()
  })

  it('keeps reconnecting when the status call itself fails (the backend is down)', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', FakeWS)
    connectSocket('ws://x/api/ws', () => undefined, () => undefined, { authStatus: () => Promise.reject(new Error('down')), onUnauthenticated: () => undefined })
    FakeWS.all[0].onclose?.()
    await vi.advanceTimersByTimeAsync(500)
    expect(FakeWS.all).toHaveLength(2)
  })

  it('a close after open reconnects without asking auth status; a stop while the status call is in flight stops it', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('WebSocket', FakeWS)
    let answer: (ok: boolean) => void = () => undefined
    const authStatus = vi.fn(() => new Promise<boolean>((r) => { answer = r }))
    const stop = connectSocket('ws://x/api/ws', () => undefined, () => undefined, { authStatus, onUnauthenticated: () => undefined })
    FakeWS.all[0].onopen?.(); FakeWS.all[0].onclose?.()
    expect(authStatus).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(500)
    expect(FakeWS.all).toHaveLength(2)
    FakeWS.all[1].onclose?.()                    // refused: the status call starts
    expect(authStatus).toHaveBeenCalledTimes(1)
    stop()
    answer(true)
    await vi.advanceTimersByTimeAsync(10000)
    expect(FakeWS.all).toHaveLength(2)
  })
})
