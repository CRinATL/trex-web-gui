import { describe, expect, it } from 'vitest'
import type { ConnectParams } from './api'
import { cpLength, portAria, portKey, portLabel, portTitle } from './portLabels'
import { wirePort } from './testWire'

const params: ConnectParams = { host: '10.0.0.5', sync_port: 4501, async_port: 4500, timeout_s: 3 }
const p0 = wirePort('idle_owned', { id: 0 })
const p1 = (mac: unknown) => wirePort('idle_owned', { id: 1, info: { ...p0.info, hw_mac: mac } })
const withMac = (mac: unknown) => wirePort('idle_owned', { id: 0, info: { ...p0.info, hw_mac: mac } })

describe('portKey', () => {
  it('is "<host>:<sync_port>" and the lower-cased MAC', () => {
    expect(portKey(params, withMac('8E:4E:97:11:BF:A8'), [p0])).toEqual({ server: '10.0.0.5:4501', key: '8e:4e:97:11:bf:a8' })
    expect(portKey(params, p0, [p0]).key).toBe('8e:4e:97:11:bf:a8')
  })
  it('falls back to #<id> for no MAC, an all-zero MAC and a repeated MAC', () => {
    const noMac = wirePort('idle_owned', { id: 3, info: {} })
    expect(portKey(params, noMac, [noMac]).key).toBe('#3')
    const zero = wirePort('idle_owned', { id: 2, info: { hw_mac: '00:00:00:00:00:00' } })
    expect(portKey(params, zero, [zero]).key).toBe('#2')
    const a = withMac('aa:bb:cc:dd:ee:ff'), b = p1('AA:BB:CC:DD:EE:FF')
    expect(portKey(params, a, [a, b]).key).toBe('#0')
    expect(portKey(params, b, [a, b]).key).toBe('#1')
  })
  it('a malformed MAC falls back too', () => {
    expect(portKey(params, withMac('aa:bb'), [p0]).key).toBe('#0')
  })
})

describe('portLabel and portTitle', () => {
  const labels = { '10.0.0.5:4501': { '8e:4e:97:11:bf:a8': { name: 'uplink-A', description: 'to the DUT' } } }
  it('finds the label of this server only', () => {
    expect(portLabel(labels, params, p0, [p0])).toEqual({ name: 'uplink-A', description: 'to the DUT' })
    expect(portLabel(labels, { ...params, host: 'localhost' }, p0, [p0])).toBeNull()
    expect(portLabel(labels, null, p0, [p0])).toBeNull()
  })
  it('titles "Port 0 — uplink-A", or "Port 0" without a name', () => {
    expect(portTitle(p0, { name: 'uplink-A', description: '' })).toBe('Port 0 — uplink-A')
    expect(portTitle(p0, null)).toBe('Port 0')
    expect(portTitle(p0, { name: '', description: 'only a note' })).toBe('Port 0')
  })
  it('the accessible name carries the description', () => {
    expect(portAria(p0, { name: 'uplink-A', description: 'to the DUT' })).toBe('Port 0 — uplink-A, to the DUT')
    expect(portAria(p0, { name: 'uplink-A', description: '' })).toBe('Port 0 — uplink-A')
  })
})

it('cpLength counts code points, as the server does', () => {
  expect(cpLength('a😀')).toBe(2)
  expect('a😀'.length).toBe(3)
})
