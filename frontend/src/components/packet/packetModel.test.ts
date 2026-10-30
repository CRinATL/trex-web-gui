import { describe, expect, it } from 'vitest'
import type { BuildResult, Definitions, PacketDoc, PacketTree } from '../../api'
import definitionsJson from '../../__fixtures__/packet/definitions.json'
import buildUdpJson from '../../__fixtures__/packet/buildUdp.json'
import treeVlanJson from '../../__fixtures__/packetTreeVlan.json'
import { appendLayer, combine, deleteLayer, layersOfTree, moveLayer, placeOf, resetField, setField } from './packetModel'

const defs = definitionsJson.definitions as unknown as Definitions
const built = buildUdpJson.udp as unknown as BuildResult

// The document buildUdp.json was recorded from (tests/contract/test_wire_fixtures.py PACKET_UDP_DOC).
const udpDoc = (): PacketDoc => ({
  fileType: 'trex-packet-editor', version: '1.0.0', metadata: { caption: '' }, fePrarameters: { cache_size: '1000' }, feInstructions: [],
  packet: [
    { id: 'Ether', fields: [{ id: 'dst', value: '52:52:18:89:91:ea' }, { id: 'src', value: '8e:4e:97:11:bf:a8' }] },
    { id: 'IP', fields: [{ id: 'src', value: '16.0.0.1' }, { id: 'dst', value: '48.0.0.1' }] },
    { id: 'UDP', fields: [{ id: 'sport', value: 1025 }, { id: 'dport', value: 12 }] },
    { id: 'Raw', fields: [{ id: 'load', value: { vtype: 'BYTES', generate: 'template_code', template_code: '78', size: 16 } }] },
  ],
})
const field = (layers: ReturnType<typeof combine>, layer: number, id: string) => layers[layer].fields.find((f) => f.id === id)!

// A copy of the build with each change applied to the field `id` of layer `layer` (the service reports `ignored` for a field
// the packet does not carry, such as ICMP ts_ori in an echo; the recorded UDP build has none).
const patched = (...changes: [layer: number, id: string, change: Record<string, unknown>][]): BuildResult => ({
  ...built,
  data: built.data.map((l, i) => ({
    ...l, fields: l.fields.map((f) => Object.assign({}, f, ...changes.filter(([layer, id]) => layer === i && id === f.id).map(([, , c]) => c))),
  })),
})

describe('combine', () => {
  it('combine marks user-set, ignored and bit fields', () => {
    const layers = combine(udpDoc(), patched([1, 'options', { ignored: true }], [1, 'flags', { value: 2 }]), defs)
    expect(layers.map((l) => l.id)).toEqual(['Ether', 'IP', 'UDP', 'Raw'])
    // user-set: only what the document holds
    expect(field(layers, 0, 'dst').userSet).toBe(true)
    expect(field(layers, 0, 'type').userSet).toBe(false)
    expect(field(layers, 1, 'ttl').userSet).toBe(false)
    expect(field(layers, 2, 'dport').userSet).toBe(true)
    // ignored
    expect(field(layers, 1, 'options').ignored).toBe(true)
    expect(field(layers, 1, 'ttl').ignored).toBe(false)
    // bit field: IP flags is a BITMASK with three groups; the DF group is set
    const flags = field(layers, 1, 'flags')
    expect(flags.bits?.map((b) => [b.name, b.current])).toEqual([['Reserved', 0], ['Fragment', 2], ['More Fragments(MF)', 0]])
    expect(field(layers, 1, 'ttl').bits).toBeNull()
    // a field of length 0 (IP version) has no byte range; others are placed in the frame
    expect(field(layers, 1, 'version').range).toBeNull()
    expect(field(layers, 0, 'dst').range).toEqual([0, 6])
    expect(field(layers, 1, 'src').range).toEqual([26, 30])
    // shown is the service's text, the definition is the protocol's
    expect(field(layers, 0, 'type').shown).toBe('IPv4')
    expect(field(layers, 0, 'type').def.type).toBe('ENUM')
    expect(layers[0].protocol?.id).toBe('Ether')
  })

  it('combine keeps the user text and the message on the field that failed', () => {
    const doc = setField(udpDoc(), 1, 'ttl', 'abc')
    const layers = combine(doc, built, defs, { kind: 'build', message: 'ValueError: bad ttl', layer: 1, field: 'ttl', instruction: null })
    const ttl = field(layers, 1, 'ttl')
    expect([ttl.shown, ttl.error, ttl.userSet]).toEqual(['abc', 'ValueError: bad ttl', true])
    expect(field(layers, 1, 'src').error).toBeNull()
  })

  it('combine with no build result lists the document\'s own fields', () => {
    const layers = combine(udpDoc(), null, defs)
    expect(layers.map((l) => l.fields.length)).toEqual([2, 2, 2, 1])
    expect(field(layers, 1, 'src')).toMatchObject({ shown: '16.0.0.1', userSet: true, range: null })
  })

  it('combine synthesises defs for a layer with no protocol def', () => {
    // a layer id the definitions do not list (an indirect Packet subclass has an id in layerIds but no ProtocolDef)
    const doc: PacketDoc = { ...udpDoc(), packet: [{ id: 'FooLayer', fields: [{ id: 'bar', value: '7' }] }] }
    const result: BuildResult = {
      binary: 'AAAA', error: null, field_engine: null,
      data: [{ id: 'FooLayer', offset: 0, real_id: 'FooLayer', valid_structure: true, fields: [{ id: 'bar', value: 7, hvalue: '7', offset: 0, length: 2 }, { id: 'baz', value: 'x', hvalue: 'x', offset: 2, length: 1 }] }],
    }
    const [layer] = combine(doc, result, defs)
    expect(layer.protocol).toBeNull()
    expect(layer.fields.map((f) => f.def)).toEqual([{ id: 'bar', name: 'bar' }, { id: 'baz', name: 'baz' }])
    expect(layer.fields.map((f) => f.userSet)).toEqual([true, false])
    expect(layer.fields[0].range).toEqual([0, 2])
  })

  it('combine does not mark user-set from a stale build whose layer at that index is another protocol', () => {
    const doc = moveLayer(udpDoc(), 2, -1)         // Ether IP UDP Raw -> Ether UDP IP Raw
    const layers = combine(doc, built, defs)
    expect(field(layers, 1, 'src').userSet).toBe(false)    // IP.src is set in the document, but index 1 is UDP there
    expect(field(layers, 0, 'dst').userSet).toBe(true)
  })
})

describe('layersOfTree', () => {
  it('layersOfTree builds read-only layers from a tree', () => {
    const tree = treeVlanJson.vlan as unknown as PacketTree
    const layers = layersOfTree(tree)
    expect(layers.map((l) => l.id)).toEqual(tree.data.map((l) => l.id))
    const all = layers.flatMap((l) => l.fields)
    expect(all.length).toBeGreaterThan(10)
    expect(all.every((f) => f.readOnly && !f.userSet && f.def.name === f.id && f.error === null)).toBe(true)
    expect(layers.every((l) => l.protocol === null)).toBe(true)
    expect(field(layers, 0, 'dst')).toMatchObject({ shown: '52:52:18:89:91:ea', range: [0, 6] })
    expect(layers[1].offset).toBe(14)
  })
})

describe('document edits', () => {
  it('setField then reset removes the field from the doc', () => {
    const doc = udpDoc()
    const set = setField(doc, 1, 'ttl', 33)
    expect(set).not.toBe(doc)
    expect(set.packet[1].fields).toEqual([...doc.packet[1].fields, { id: 'ttl', value: 33 }])
    expect(doc.packet[1].fields).toHaveLength(2)             // the original is untouched
    expect(setField(set, 1, 'ttl', 33)).toBe(set)            // the same value changes nothing
    expect(setField(set, 1, 'ttl', 34).packet[1].fields.at(-1)).toEqual({ id: 'ttl', value: 34 })
    expect(resetField(set, 1, 'ttl')).toEqual(doc)
    expect(resetField(doc, 1, 'ttl')).toBe(doc)              // nothing to reset
    expect(setField(doc, 9, 'ttl', 1)).toBe(doc)             // no such layer
  })

  it('moveLayer never moves the first layer', () => {
    const doc = udpDoc()
    expect(moveLayer(doc, 0, 1)).toBe(doc)
    expect(moveLayer(doc, 1, -1)).toBe(doc)                  // nothing moves above the first layer
    expect(moveLayer(doc, 3, 1)).toBe(doc)
    expect(moveLayer(doc, 1, 1).packet.map((l) => l.id)).toEqual(['Ether', 'UDP', 'IP', 'Raw'])
    expect(deleteLayer(doc, 0)).toBe(doc)
    expect(deleteLayer(doc, 2).packet.map((l) => l.id)).toEqual(['Ether', 'IP', 'Raw'])
  })

  it('appendLayer adds an empty layer at the end', () => {
    expect(appendLayer(udpDoc(), 'ICMP').packet.at(-1)).toEqual({ id: 'ICMP', fields: [] })
  })

  it('placeOf names the field, the layer or the instruction an edit changed', () => {
    const doc = udpDoc()
    expect(placeOf(doc, setField(doc, 2, 'sport', 7))).toEqual({ layer: 2, field: 'sport', instruction: null })
    expect(placeOf(doc, appendLayer(doc, 'ICMP'))).toEqual({ layer: 4, field: null, instruction: null })
    expect(placeOf(doc, { ...doc, feInstructions: [{ id: 'STLVmFixIpv4', parameters: {} }] })).toEqual({ layer: null, field: null, instruction: 0 })
    expect(placeOf(doc, doc)).toBeNull()
  })
})
