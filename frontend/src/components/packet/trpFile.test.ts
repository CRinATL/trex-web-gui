/// <reference types="node" />
import { readdirSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { newDoc, readTrp, TrpError, writeTrp } from './trpFile'

// The templates the vendored TRex client ships: real legacy files, one per server template.
const DIR = resolve(import.meta.dirname, '../../../../backend/trex_web_gui/_vendor/trex/scapy_server/templates')
const templates = readdirSync(DIR, { recursive: true, encoding: 'utf8' }).filter((p) => p.endsWith('.trp')).map((p) => [p, readFileSync(resolve(DIR, p), 'utf8')] as const)

const text = (extra: Record<string, unknown>) => JSON.stringify({ fileType: 'trex-packet-editor', version: '1.0.0', metadata: { caption: 'x' }, packet: [{ id: 'Ether', fields: [] }], feInstructions: [], ...extra })

describe('trpFile', () => {
  it('reads both spellings, writes the legacy one', () => {
    const legacy = readTrp(text({ fePrarameters: { cache_size: '7' } }))
    const fixed = readTrp(text({ feParameters: { cache_size: '7' } }))
    expect(fixed).toEqual(legacy)
    expect(legacy.fePrarameters).toEqual({ cache_size: '7' })
    const out = JSON.parse(writeTrp(fixed)) as Record<string, unknown>
    expect(out.fePrarameters).toEqual({ cache_size: '7' })
    expect('feParameters' in out).toBe(false)
    expect(Object.keys(out)).toEqual(['fileType', 'version', 'metadata', 'packet', 'fePrarameters', 'feInstructions'])
    expect(() => readTrp(text({ fePrarameters: {}, feParameters: {} }))).toThrow(TrpError)
  })
  it('refuses what is not a packet file', () => {
    expect(() => readTrp('nope')).toThrow(/not JSON/)
    expect(() => readTrp('[]')).toThrow(/JSON object/)
    expect(() => readTrp(JSON.stringify({ packet: [{ id: 'Ether' }] }))).toThrow(/fileType/)
    expect(() => readTrp(text({ packet: [] }))).toThrow(/at least one layer/)
    expect(() => readTrp(text({ packet: [{ fields: [] }] }))).toThrow(/layer 0/)
  })
  it('missing Field Engine keys and layer fields default to empty', () => {
    const d = readTrp(JSON.stringify({ fileType: 'trex-packet-editor', packet: [{ id: 'Ether' }] }))
    expect(d).toEqual({ ...newDoc(), fePrarameters: {} })
  })
  it('round trip of every server template fixture', () => {
    expect(templates.length).toBe(14)
    for (const [, body] of templates) expect(JSON.parse(writeTrp(readTrp(body)))).toEqual(JSON.parse(body))
  })
})
