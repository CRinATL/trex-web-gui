import { describe, expect, it } from 'vitest'
import type { Definitions, PacketDoc } from '../../api'
import defsFixture from '../../__fixtures__/packet/definitions.json'
import { addInstruction, deleteInstruction, fromTemplate, setGlobal, setParam } from './feModel'

const defs = (defsFixture as unknown as { definitions: Definitions }).definitions
const doc = (): PacketDoc => ({
  fileType: 'trex-packet-editor', version: '1.0.0', metadata: { caption: '' }, fePrarameters: {}, feInstructions: [],
  packet: [{ id: 'Ether', fields: [] }, { id: 'IP', fields: [] }, { id: 'UDP', fields: [] }],
})

describe('feModel', () => {
  it('add takes meta defaults as strings', () => {
    const d = addInstruction(doc(), 'STLVmFlowVar', defs)
    expect(d.feInstructions).toEqual([{ id: 'STLVmFlowVar', parameters: {
      name: 'Not defined', init_value: '0', max_value: '0', min_value: '0', step: '1', size: '4', op: 'inc', value_list: 'None' } }])
    for (const v of Object.values(d.feInstructions[0].parameters)) expect(typeof v).toBe('string')
    expect(addInstruction(doc(), 'NoSuch', defs).feInstructions).toEqual([])
  })

  it('simple_flow_var template names and offsets the field', () => {
    const d = fromTemplate(doc(), 'simple_flow_var', 'IP', 'src', defs)
    expect(d.feInstructions.map((i) => i.id)).toEqual(['STLVmFlowVar', 'STLVmWrFlowVar'])
    expect(d.feInstructions[0].parameters.name).toBe('IP_src')
    expect(d.feInstructions[0].parameters.op).toBe('inc')
    expect(d.feInstructions[1].parameters).toMatchObject({ fv_name: 'IP_src', pkt_offset: 'IP.src', is_big: 'true' })
    const r = fromTemplate(doc(), 'rep_rand_var', 'IP', 'ttl', defs)
    expect(r.feInstructions.map((i) => i.id)).toEqual(['STLVmFlowVarRepeatableRandom', 'STLVmWrFlowVar'])
    expect(r.feInstructions[1].parameters).toMatchObject({ fv_name: 'IP_ttl', pkt_offset: 'IP.ttl' })
    expect(fromTemplate(doc(), 'nope', 'IP', 'src', defs).feInstructions).toEqual([])
  })

  it('a layer id that occurs twice is addressed by its place', () => {
    const two: PacketDoc = { ...doc(), packet: [{ id: 'Ether', fields: [] }, { id: 'IP', fields: [] }, { id: 'IP', fields: [] }] }
    expect(fromTemplate(two, 'simple_flow_var', 'IP', 'src', defs, 2).feInstructions[1].parameters.pkt_offset).toBe('IP:1.src')
  })

  it('delete removes one instruction', () => {
    let d = addInstruction(addInstruction(doc(), 'STLVmFlowVar', defs), 'STLVmWrFlowVar', defs)
    d = deleteInstruction(d, 0)
    expect(d.feInstructions.map((i) => i.id)).toEqual(['STLVmWrFlowVar'])
    expect(deleteInstruction(d, 5)).toBe(d)
  })

  it('setParam and setGlobal store strings and leave an unchanged document alone', () => {
    const d = addInstruction(doc(), 'STLVmFlowVar', defs)
    const e = setParam(d, 0, 'op', 'dec')
    expect(e.feInstructions[0].parameters.op).toBe('dec')
    expect(d.feInstructions[0].parameters.op).toBe('inc')
    expect(setParam(e, 0, 'op', 'dec')).toBe(e)
    const g = setGlobal(doc(), 'cache_size', '10')
    expect(g.fePrarameters).toEqual({ cache_size: '10' })
    expect(setGlobal(g, 'cache_size', '').fePrarameters).toEqual({})
  })
})
