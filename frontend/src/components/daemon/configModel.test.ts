import { describe, expect, it } from 'vitest'
import metadata from '../../__fixtures__/daemonMetadata.json'
import { addItem, defaultsOf, fieldError, getAt, readConnectInfo, removeItem, setAt, type MetaField } from './configModel'

const META = metadata as unknown as MetaField[]

describe('configModel', () => {
  it('setAt is immutable', () => {
    const before = { version: 2, memory: { mbuf_64: 1 }, interfaces: ['a', 'b'] }
    const snapshot = JSON.parse(JSON.stringify(before))
    const after = setAt(before, ['memory', 'mbuf_64'], 5)
    expect(before).toEqual(snapshot)
    expect(after).toEqual({ version: 2, memory: { mbuf_64: 5 }, interfaces: ['a', 'b'] })
    expect(after.memory).not.toBe(before.memory)
    expect(after.interfaces).toBe(before.interfaces)   // untouched branches are shared
    expect(setAt(before, ['interfaces', 1], 'c').interfaces).toEqual(['a', 'c'])
    expect(before.interfaces).toEqual(['a', 'b'])
    // a path through nothing creates it; undefined removes the key
    expect(setAt({}, ['port_info', 0, 'ip'], '1.1.1.1')).toEqual({ port_info: [{ ip: '1.1.1.1' }] })
    expect('version' in setAt(before, ['version'], undefined)).toBe(false)
  })

  it('list items without ids', () => {
    const portInfo = META.find((f) => f.id === 'port_info')!
    expect(portInfo.item).toBeDefined()
    expect(portInfo.item!.id).toBeUndefined()
    const one = addItem({ version: 2 }, ['interfaces'], 'veth0')
    const two = addItem(one, ['interfaces'], 'veth1')
    expect(two.interfaces).toEqual(['veth0', 'veth1'])
    expect(one.interfaces).toEqual(['veth0'])
    expect(removeItem(two, ['interfaces'], 0).interfaces).toEqual(['veth1'])
    expect(two.interfaces).toEqual(['veth0', 'veth1'])
    expect(getAt(two, ['interfaces', 1])).toBe('veth1')
  })

  it('fieldError matches dotted paths', () => {
    const errors = [
      { path: 'port_info.0.default_gw', line: null, message: 'must be an IP address' },
      { path: 'interfaces', line: null, message: 'has 1 entries but port_limit is 2' },
    ]
    expect(fieldError(errors, ['port_info', 0, 'default_gw'])).toBe('must be an IP address')
    expect(fieldError(errors, ['interfaces'])).toBe('has 1 entries but port_limit is 2')
    expect(fieldError(errors, ['port_info', 1, 'default_gw'])).toBeNull()
    expect(fieldError(errors, ['port_info'])).toBeNull()   // a parent does not inherit its children's errors
  })

  it('defaultsOf mirrors the backend: version 2, the ports, no key without a default', () => {
    const d = defaultsOf(META)
    expect(d.version).toBe(2)
    expect(d.zmq_pub_port).toBe(4500)
    expect(d.zmq_rpc_port).toBe(4501)
    expect('port_limit' in d).toBe(false)
  })

  it('defaultsOf leaves optional sections and c to TRex (F1: a platform stub and c: 4 are refused)', () => {
    const d = defaultsOf(META)
    expect('platform' in d || 'memory' in d || 'tw' in d).toBe(false)
    expect('c' in d).toBe(false)
    const meta: MetaField[] = [
      { id: 's', name: 'S', type: 'OBJECT', mandatory: true, attributes: [{ id: 'x', name: 'X', type: 'NUMBER', default: 1 }] },
      { id: 'o', name: 'O', type: 'OBJECT', mandatory: false, attributes: [{ id: 'y', name: 'Y', type: 'NUMBER', default: 2 }] },
    ]
    expect(defaultsOf(meta)).toEqual({ s: { x: 1 } })
  })

  it('readConnectInfo reads the connect reply and refuses a malformed one', () => {
    expect(readConnectInfo({})).toBeNull()
    const info = readConnectInfo({
      metadata: META, metadata_version: 'abc', config_yaml: '- version: 2\n', files_path: '/tmp/trex_files',
      devices: [{ Slot_str: '00:03.0', Interface: 'ens3', Device_str: 'Virtio network device', Active: '*Active*' }, { id: 'dummy' }],
    })
    expect(info?.devices.map((d) => d.id)).toEqual(['00:03.0', 'dummy'])
    expect(info?.devices[0].label).toContain('ens3')
    expect(info?.filesPath).toBe('/tmp/trex_files')
    expect(readConnectInfo({ metadata: 'nope' })).toBeNull()
  })
})
