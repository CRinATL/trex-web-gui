// The recorded p3c wire fixtures that carry no literal-typed discriminants (JSON widens
// those to string) must satisfy the api.ts types. tsc -b (npm run build)
// type-checks this file, so a type that drifts from the recorded wire shape fails the build.
import { describe, expect, it } from 'vitest'
import type { BuildResult, Decompiled, Definitions, DiffReply, FeInstruction, FeParamMeta, FieldDef, LibraryList, PacketTree, SimReport, Source, SpoolListing, StreamMode, StreamPage, TemplateList } from './api'
import captureFiles from './__fixtures__/captureFiles.json'
import packetTree from './__fixtures__/packetTree.json'
import packetTreeVlan from './__fixtures__/packetTreeVlan.json'
import packetBuildError from './__fixtures__/packet/buildError.json'
import packetBuildUdp from './__fixtures__/packet/buildUdp.json'
import packetDecompile from './__fixtures__/packet/decompile.json'
import packetDefinitions from './__fixtures__/packet/definitions.json'
import packetTemplates from './__fixtures__/packet/templates.json'
import studioLibrary from './__fixtures__/studioLibrary.json'
import studioSim from './__fixtures__/studioSim.json'
import studioStreams from './__fixtures__/studioStreams.json'

describe('wire fixtures against api.ts types', () => {
  it('each recorded fixture is assignable to its type', () => {
    const checked = [captureFiles.listing satisfies SpoolListing, packetTree.udp satisfies PacketTree]
    expect(checked).toHaveLength(2)
  })

  it('the udp tree carries BYTES values, which the type must admit', () => {
    const tree: PacketTree = packetTree.udp
    const vals = tree.data.flatMap(l => l.fields).map(f => f.value)
    expect(vals.some(v => typeof v === 'object' && 'base64' in v)).toBe(true)
  })

  // JSON widens a string literal to string, so the few literal-typed discriminants are narrowed one by one; every other field is checked.
  it('the recorded Studio fixtures satisfy their types', () => {
    const lib = studioLibrary.udp_for_benchmarks
    const list = { ...lib, profiles: lib.profiles.map((p) => ({ ...p, source: { ...p.source, kind: p.source.kind as Source['kind'] } })) } satisfies LibraryList
    const pg = studioStreams.udp_for_benchmarks
    const page = { ...pg, rows: pg.rows.map((r) => ({ ...r, stream: { ...r.stream, mode: { ...r.stream.mode, type: r.stream.mode.type as StreamMode['type'], rate: { ...r.stream.mode.rate, type: r.stream.mode.rate.type as StreamMode['rate']['type'] } } } })) } satisfies StreamPage
    const sim = { ...studioSim.udp_for_benchmarks, chains: studioSim.udp_for_benchmarks.chains.map((c) => ({ ...c, end: c.end as 'stop' | 'loop' })) } satisfies SimReport
    const diff = { rev: 'r', stream_count: 1, renamed: {}, cleared: [], warnings: [] } satisfies DiffReply
    expect([list.profiles.length, page.rows.length, sim.streams.length, diff.stream_count]).toEqual([1, 1, 1, 1])
  })

  // P4b Task 6. Same narrowing as above: only the string-literal discriminants are cast, every other key is checked.
  it('the recorded packet fixtures satisfy their types', () => {
    // TypeScript merges the objects of a JSON array into one shape with the missing keys as `undefined`; a record type
    // cannot hold those, so records are rebuilt from their defined entries (the data is unchanged).
    const rec = <T,>(o: { [k: string]: T | undefined } | undefined): Record<string, T> | undefined =>
      o && (Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined)) as Record<string, T>)
    const d = packetDefinitions.definitions
    const meta = (m: (typeof d.feInstructionParameters)[number]): FeParamMeta => ({ ...m, type: m.type as FeParamMeta['type'], dict: rec<string | number>(m.dict) })
    const defs = {
      ...d,
      protocols: d.protocols.map((p) => ({ ...p, fields: p.fields.map((f): FieldDef => ({ ...f, type: f.type as FieldDef['type'], values_dict: rec<number>(f.values_dict) })) })),
      feInstructionParameters: d.feInstructionParameters.map(meta),
      feParameters: d.feParameters.map(meta),
    } satisfies Definitions
    const udp = packetBuildUdp.udp satisfies BuildResult
    const failed = packetBuildError.expression satisfies BuildResult
    const list = packetTemplates.listing satisfies TemplateList
    const dec = {
      ...packetDecompile.flow_var,
      instructions: packetDecompile.flow_var.instructions.map((i): FeInstruction => ({ ...i, parameters: rec<string>(i.parameters) ?? {} })),
    } satisfies Decompiled
    expect([defs.layerIds.length > 300, defs.etag.length, udp.error, failed.binary, list.server.length > 5, dec.instructions.length]).toEqual([true, 16, null, null, true, 2])
    // the vlan tree the viewer test uses is the recorded decode (pinned by the contract test, p3c-capture-114)
    expect(packetTreeVlan.vlan satisfies PacketTree).toBeTruthy()
  })
})
