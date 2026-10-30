import { describe, expect, it } from 'vitest'
import type { PortView } from './api'
import { captureBlock, serviceOffPorts } from './captureGate'

const port = (id: number, service: boolean): PortView => ({ id, service } as PortView)
const ports = [port(0, false), port(1, true), port(2, false)]
const ok = { connected: true, readOnly: false, monitorActive: false, rxPorts: [1], ports }

describe('captureBlock', () => {
  it('is null when everything allows a capture', () => expect(captureBlock(ok)).toBeNull())
  it('names the reasons in the order: not connected, read-only, a monitor runs, service mode', () => {
    const all = { connected: false, readOnly: true, monitorActive: true, rxPorts: [0], ports }
    expect(captureBlock(all)).toBe('not connected')
    expect(captureBlock({ ...all, connected: true })).toBe('read-only session')
    expect(captureBlock({ ...all, connected: true, readOnly: false })).toBe('a monitor is already running')
    expect(captureBlock({ ...all, connected: true, readOnly: false, monitorActive: false })).toBe('Rx port(s) 0 are not in service mode')
  })
  it('lists every Rx port that is off service, and ignores a Tx-only capture', () => {
    expect(captureBlock({ ...ok, rxPorts: [0, 1, 2] })).toBe('Rx port(s) 0, 2 are not in service mode')
    expect(captureBlock({ ...ok, rxPorts: [] })).toBeNull()
  })
})

describe('serviceOffPorts', () => {
  it('reads PortView.service, in the order asked', () => expect(serviceOffPorts([2, 1, 0], ports)).toEqual([2, 0]))
  it('counts a port the server has not listed as off', () => expect(serviceOffPorts([5], ports)).toEqual([5]))
})
