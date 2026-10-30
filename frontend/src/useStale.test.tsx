import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AppStateProvider } from './state'
import { useStale, type StaleSource } from './useStale'
import * as ws from './ws'

// A read that last delivered at t = 0 on the (faked) monotonic clock, with a 1 s period: stale after 3 s, busy or not.
const source: StaleSource = { receivedAt: () => 0, interval: () => 1, subscribe: () => () => undefined }
function Probe() { return <span data-testid="s">{String(useStale(source))}</span> }

let onMsg: (m: unknown) => void = () => undefined
beforeEach(() => {
  vi.useFakeTimers()
  vi.spyOn(ws, 'connectSocket').mockImplementation((_url, m) => { onMsg = m; return () => undefined })
  render(<AppStateProvider><Probe /></AppStateProvider>)
  act(() => onMsg({ type: 'session', state: 'connected', params: null, info: null, error: null, rpc_console: false, include_polling: false, connect_seq: 'u:1' }))
})
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

const shown = () => screen.getByTestId('s').textContent
const advance = (ms: number) => act(() => { vi.advanceTimersByTime(ms) })

describe('useStale with a port busy', () => {
  it('control: with nothing busy the marker shows after 3 s', () => {
    advance(3500)
    expect(shown()).toBe('false')
    advance(1000)
    expect(shown()).toBe('true')
  })
  it.each([['ping', 0], ['apply', 1], ['scan', 0]])('a port busy with %s does not widen the threshold: stale after 3 x interval', (kind, port) => {
    act(() => onMsg({ type: 'busy', ports: { [port]: kind } }))
    advance(3500)
    expect(shown()).toBe('false')
    advance(1000)    // 4.5 s: past 3 s
    expect(shown()).toBe('true')
  })
  it('ending busy does not delay the marker either', () => {
    act(() => onMsg({ type: 'busy', ports: { 0: 'scan' } }))
    advance(3500)
    act(() => onMsg({ type: 'busy', ports: {} }))
    advance(1000)
    expect(shown()).toBe('true')
  })
})
