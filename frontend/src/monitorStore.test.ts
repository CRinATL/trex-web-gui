import { act, render, screen } from '@testing-library/react'
import { createElement } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { MonitorMsg } from './api'
import { CAPACITY, LATENCY_WINDOW_S, monitorStore, useMonitor } from './monitorStore'
import stl from './__fixtures__/monitorStl.json'
import stateful from './__fixtures__/monitorStateful.json'
import { AppStateProvider } from './state'
import * as ws from './ws'

const base = stl as unknown as MonitorMsg
const at = (ts: number, over: Partial<MonitorMsg> = {}): MonitorMsg => ({ ...base, ts, ...over })

afterEach(() => { vi.restoreAllMocks() })

describe('monitorStore', () => {
  it('history keeps 300 points', () => {
    for (let i = 0; i < CAPACITY + 25; i++) monitorStore.push(at(1000 + i, { global: { ...base.global, m_tx_bps: i } }))
    const h = monitorStore.history()
    expect(h.ts).toHaveLength(300)
    expect(h.tx_bps).toHaveLength(300)
    expect(h.ts[0]).toBe(1025)                       // the oldest 25 went
    expect(h.tx_bps.at(-1)).toBe(CAPACITY + 24)
    expect(h.cpu[0]).toBe(0.05709)                    // m_cpu_util of the recorded trex-global
  })

  it('appends a point only when ts changes', () => {
    monitorStore.push(at(1000))
    monitorStore.push(at(1000, { state: 'stale' }))   // a state change republishes the same ts
    monitorStore.push(at(1000.5))
    expect(monitorStore.history().ts).toEqual([1000, 1000.5])
  })

  it('latency window keeps 16 s per side', () => {
    const lat = (c: number, s: number) => ({ 'c-max-0': c, 'c-max-1': s, 'c-max-2': c - 1, 'c-max-3': s + 5, 'max-0': 9999, 'max-1': 9999, 'avg-0': 1 })
    for (let i = 0; i <= 40; i++) monitorStore.push(at(2000 + i, { latency: lat(i, 100 + i) }))
    const w = monitorStore.latencyWindow()
    expect(w.ts[0]).toBeGreaterThan(2040 - LATENCY_WINDOW_S)
    expect(w.ts.at(-1)).toBe(2040)
    expect(w.ts).toHaveLength(16)
    expect(w.client.at(-1)).toBe(40)                  // the larger of ports 0 and 2
    expect(w.server.at(-1)).toBe(145)                 // the larger of ports 1 and 3
    expect(w.client).toHaveLength(w.server.length)
  })

  it('the window max goes down once a spike leaves the 16 s window', () => {
    // the all-time `max-N` (a constant 5000 here) must not be read: only the per-period `c-max-N`
    const lat = (c: number) => ({ 'c-max-0': c, 'c-max-1': c, 'max-0': 5000, 'max-1': 5000 })
    for (let i = 0; i <= 40; i++) monitorStore.push(at(3000 + i, { latency: lat(i === 5 ? 900 : 10) }))
    const w = monitorStore.latencyWindow()
    expect(w.ts[0]).toBeGreaterThan(3040 - LATENCY_WINDOW_S)
    expect(Math.max(...w.client as number[])).toBe(10)
    expect(Math.max(...w.server as number[])).toBe(10)
    monitorStore.reset()
    for (let i = 0; i <= 10; i++) monitorStore.push(at(4000 + i, { latency: lat(i === 5 ? 900 : 10) }))
    expect(Math.max(...monitorStore.latencyWindow().client as number[])).toBe(900)   // the spike is still inside the window
  })

  it('has no latency points before a latency message arrives', () => {
    monitorStore.push(base)
    expect(monitorStore.latencyWindow().ts).toEqual([])
  })

  it('pulse() grows when frames grow', () => {
    monitorStore.push(at(1, { frames: 1 }))
    const p = monitorStore.pulse()
    monitorStore.push(at(1, { frames: 1, state: 'stale' }))
    expect(monitorStore.pulse()).toBe(p)
    monitorStore.push(at(2, { frames: 2 }))
    expect(monitorStore.pulse()).toBe(p + 1)
  })

  it('a new target resets, the same target does not', () => {
    monitorStore.push(at(1)); monitorStore.push(at(2))
    expect(monitorStore.history().ts).toHaveLength(2)
    monitorStore.push(at(3, { state: 'disconnected', target: null }))      // a disconnect names no target, and keeps the history
    monitorStore.push(at(4))                                               // same target again
    expect(monitorStore.history().ts).toEqual([1, 2, 3, 4])
    monitorStore.push(at(5, { target: { host: '10.0.0.9', port: 4500 } }))
    expect(monitorStore.history().ts).toEqual([5])
    expect(monitorStore.get()?.target?.host).toBe('10.0.0.9')
  })

  it('a Control reconnect keeps the Monitor', () => {
    let onMsg: (m: unknown) => void = () => undefined
    vi.spyOn(ws, 'connectSocket').mockImplementation((_url, m) => { onMsg = m; return () => undefined })
    const session = (seq: string, state: string) => ({ type: 'session', state, params: null, info: null, error: null, rpc_console: false, include_polling: false, connect_seq: seq })
    function Probe() { return createElement('span', { 'data-testid': 'frames' }, useMonitor((m) => m?.frames ?? -1)) }
    render(createElement(AppStateProvider, null, createElement(Probe)))
    act(() => onMsg(session('a:1', 'connected')))
    act(() => onMsg(stateful))
    act(() => onMsg({ ...stateful, ts: stateful.ts + 1 }))
    expect(screen.getByTestId('frames')).toHaveTextContent(String(stateful.frames))
    expect(monitorStore.history().ts).toHaveLength(2)
    // Control drops and connects again: a new connect_seq, state going through connecting and disconnected
    act(() => onMsg(session('a:2', 'connecting')))
    act(() => onMsg(session('a:2', 'disconnected')))
    act(() => onMsg(session('a:3', 'connected')))
    expect(screen.getByTestId('frames')).toHaveTextContent(String(stateful.frames))
    expect(monitorStore.history().ts).toHaveLength(2)
    expect(monitorStore.get()?.templates).toEqual(stateful.templates)
  })
})
