import { render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from './api'
import { Root } from './Root'
import * as ws from './ws'

// A recording BroadcastChannel; instances of one name hear each other's posts.
function fakeChannelClass() {
  return class Fake {
    static all: Fake[] = []
    onmessage: ((e: { data: unknown }) => void) | null = null
    posted: { type: string }[] = []
    closed = false
    name: string
    constructor(name: string) { this.name = name; Fake.all.push(this) }
    postMessage(data: { type: string }) {
      this.posted.push(data)
      for (const c of Fake.all) if (c !== this && !c.closed) c.onmessage?.({ data })
    }
    close() { this.closed = true }
  }
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('Root', () => {
  it('the pop-out mounts no provider and never publishes', () => {
    const Fake = fakeChannelClass()
    vi.stubGlobal('BroadcastChannel', Fake)
    const socket = vi.spyOn(ws, 'connectSocket').mockReturnValue(() => undefined)
    const auth = vi.spyOn(api, 'authStatus').mockResolvedValue(true)
    render(<Root pathname="/events/popout" />)
    expect(socket).not.toHaveBeenCalled()
    expect(auth).not.toHaveBeenCalled()
    expect(Fake.all.flatMap((c) => c.posted.map((m) => m.type))).toEqual(['hello'])
  })
  it('any other path mounts the provider, which opens the socket and publishes the log', () => {
    const Fake = fakeChannelClass()
    vi.stubGlobal('BroadcastChannel', Fake)
    const socket = vi.spyOn(ws, 'connectSocket').mockReturnValue(() => undefined)
    vi.spyOn(api, 'authStatus').mockResolvedValue(true)
    render(<Root pathname="/control" />)
    expect(socket).toHaveBeenCalledTimes(1)
    expect(Fake.all.flatMap((c) => c.posted.map((m) => m.type))).toContain('log')
  })
})
