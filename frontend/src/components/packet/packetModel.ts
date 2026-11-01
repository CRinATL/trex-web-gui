// The packet editor's model, pure (no React): document edits, and the join of the user's document with a build result
// and the protocol definitions that the field rows show. The document is the user's intent (only what they set); the
// build result is what the service made of it (every field, with its bytes).
import type { BitDef, BuildResult, Definitions, FeInstruction, FeValue, FieldDef, FieldValue, PacketDoc, PacketLayer, PacketTree, ProtocolDef } from '../../api'
import { fieldRange, layerEnds, type Range } from './hexModel'

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

// -- document edits: each returns a new document, or the same object when the edit changes nothing --

const withLayer = (doc: PacketDoc, i: number, fields: PacketDoc['packet'][number]['fields']): PacketDoc =>
  ({ ...doc, packet: doc.packet.map((l, j) => (j === i ? { ...l, fields } : l)) })

// Set (or add) a field of layer `layer`.
export function setField(doc: PacketDoc, layer: number, id: string, value: FieldValue): PacketDoc {
  const l = doc.packet[layer]
  if (!l) return doc
  const at = l.fields.findIndex((f) => f.id === id)
  if (at >= 0 && same(l.fields[at].value, value)) return doc
  return withLayer(doc, layer, at >= 0 ? l.fields.map((f, k) => (k === at ? { id, value } : f)) : [...l.fields, { id, value }])
}

// Drop a field from the document, so the service's default shows again.
export function resetField(doc: PacketDoc, layer: number, id: string): PacketDoc {
  const l = doc.packet[layer]
  if (!l || !l.fields.some((f) => f.id === id)) return doc
  return withLayer(doc, layer, l.fields.filter((f) => f.id !== id))
}

export const appendLayer = (doc: PacketDoc, id: string): PacketDoc => ({ ...doc, packet: [...doc.packet, { id, fields: [] }] })

// Move layer `index` one place up (-1) or down (1). The first layer never moves and nothing moves above it.
export function moveLayer(doc: PacketDoc, index: number, dir: -1 | 1): PacketDoc {
  const to = index + dir
  if (index < 1 || index >= doc.packet.length || to < 1 || to >= doc.packet.length) return doc
  const packet = [...doc.packet]
  ;[packet[index], packet[to]] = [packet[to], packet[index]]
  return { ...doc, packet }
}

// Delete layer `index`; the first layer is never deleted. Field Engine instructions are left alone (plan D2): one that
// now points at a missing field fails the next build, as that instruction's error.
export function deleteLayer(doc: PacketDoc, index: number): PacketDoc {
  if (index < 1 || index >= doc.packet.length) return doc
  return { ...doc, packet: doc.packet.filter((_, i) => i !== index) }
}

// Replace the Field Engine instructions and/or the global parameters.
export function setFe(doc: PacketDoc, fe: { feInstructions?: FeInstruction[]; fePrarameters?: Record<string, FeValue> }): PacketDoc {
  const next = { ...doc, ...(fe.feInstructions ? { feInstructions: fe.feInstructions } : {}), ...(fe.fePrarameters ? { fePrarameters: fe.fePrarameters } : {}) }
  return same(next, doc) ? doc : next
}

// Where an edit changed the document: a layer and field, a layer, a Field Engine instruction; null for none or for several places.
export interface Place { layer: number | null; field: string | null; instruction: number | null }
export function placeOf(before: PacketDoc, after: PacketDoc): Place | null {
  const n = Math.min(before.packet.length, after.packet.length)
  for (let i = 0; i < n; i++) {
    const a = before.packet[i], b = after.packet[i]
    if (a.id !== b.id) return { layer: i, field: null, instruction: null }
    const ids = [...new Set([...a.fields.map((f) => f.id), ...b.fields.map((f) => f.id)])]
    const changed = ids.filter((id) => !same(a.fields.find((f) => f.id === id)?.value, b.fields.find((f) => f.id === id)?.value))
    if (changed.length > 0) return { layer: i, field: changed[0], instruction: null }
  }
  if (before.packet.length !== after.packet.length) return { layer: n, field: null, instruction: null }
  const m = Math.max(before.feInstructions.length, after.feInstructions.length)
  for (let i = 0; i < m; i++) if (!same(before.feInstructions[i], after.feInstructions[i])) return { layer: null, field: null, instruction: i }
  return null
}

// The edit appended a layer: the old layers are a prefix of the new, and there is one more.
export const isAppend = (before: PacketDoc, after: PacketDoc): boolean =>
  after.packet.length === before.packet.length + 1 && before.packet.every((l, i) => same(l, after.packet[i])) &&
  same({ ...before, packet: 0 }, { ...after, packet: 0 })

// -- the combined view --

// Why the latest edit has no good build. `layer`, `field` and `instruction` say where, when that is known.
export interface EditorError extends Place {
  kind: 'build' | 'fe' | 'restart' | 'request' | 'append'
  message: string
}

export interface BitGroup extends BitDef { current: number }
export interface CombinedField {
  id: string
  def: FieldDef                  // the protocol's definition, or {id, name: id} (edited as text) when it has none
  value: PacketLayer['fields'][number]['value'] | null   // as built; null before the first build
  docValue?: FieldValue          // what the user's document sets (absent when it does not); the built value of an expression is its result
  shown: string                  // the text the row shows: the built value, or the user's text while the field is in error
  userSet: boolean               // the user's document sets it
  ignored: boolean               // not in the packet as built
  readOnly: boolean              // a tree from a capture: nothing to edit
  range: Range | null            // byte range in the frame, clipped to the layer
  bits: BitGroup[] | null        // a BITMASK field's groups with their current state
  error: string | null
}
export interface CombinedLayer {
  index: number
  id: string
  realId: string | null          // what the bytes decode as (title "as <real_id>"); null: no such layer in the bytes
  offset: number
  validStructure: boolean
  protocol: ProtocolDef | null   // null when the layer has no definition (an indirect subclass)
  fields: CombinedField[]
  error: string | null
}

// A value as the user would type it.
export function textOf(v: FieldValue | undefined | null): string {
  if (v === undefined || v === null) return ''
  if (typeof v === 'string') return v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  if (Array.isArray(v)) return JSON.stringify(v)
  return v.vtype === 'EXPRESSION' ? (v.expr ?? '') : JSON.stringify(v)
}

function groupsOf(def: FieldDef, value: unknown): BitGroup[] | null {
  if (def.type !== 'BITMASK' || !def.bits) return null
  const n = typeof value === 'number' ? value : 0
  return def.bits.map((b) => ({ ...b, current: n & b.mask }))
}

// `doc` joined with a build `result` (normally the last good one) and the definitions. `failure` marks the field the
// latest edit could not build; that field keeps the user's text. With no built data the layers come from the document alone.
export function combine(doc: PacketDoc, result: BuildResult | null, defs: Definitions, failure: EditorError | null = null): CombinedLayer[] {
  const protocols = new Map(defs.protocols.map((p) => [p.id, p]))
  const built = result?.data ?? []
  const ends = result?.binary ? layerEnds({ binary: result.binary, data: built }) : built.map((_, i) => (i + 1 < built.length ? built[i + 1].offset : 0))   // no binary: the last layer has no extent
  if (built.length === 0) {
    return doc.packet.map((dl, i) => {
      const protocol = protocols.get(dl.id) ?? null
      const fields = dl.fields.map((f): CombinedField => {
        const def = protocol?.fields.find((d) => d.id === f.id) ?? { id: f.id, name: f.id }
        const bad = failure?.layer === i && failure.field === f.id
        return { id: f.id, def, value: null, docValue: f.value, shown: textOf(f.value), userSet: true, ignored: false, readOnly: false, range: null, bits: null, error: bad ? failure.message : null }
      })
      return { index: i, id: dl.id, realId: dl.id, offset: 0, validStructure: true, protocol, fields, error: failure?.layer === i && failure.field === null ? failure.message : null }
    })
  }
  return built.map((l, i) => {
    // The user's layer at this index, when it is the same layer (a stale build may not be; Padding is reported as Raw).
    const cand = doc.packet[i]
    const dl = cand && (cand.id === l.id || l.id === 'Raw') ? cand : undefined
    const protocol = protocols.get(dl?.id ?? l.id) ?? protocols.get(l.id) ?? null
    const end = ends[i]
    const fields = l.fields.map((f, k): CombinedField => {
      const def = protocol?.fields.find((d) => d.id === f.id) ?? { id: f.id, name: f.id }
      const docField = dl?.fields.find((d) => d.id === f.id)
      const bad = failure?.layer === i && failure.field === f.id
      const range: Range | null = fieldRange(l.fields, k, l.offset, end)
      return {
        id: f.id, def, value: f.value, docValue: docField?.value, userSet: docField !== undefined, ignored: f.ignored === true, readOnly: false, range,
        shown: bad ? textOf(docField?.value) : String(f.hvalue ?? ''), bits: groupsOf(def, f.value), error: bad ? failure.message : null,
      }
    })
    return { index: i, id: l.id, realId: l.real_id, offset: l.offset, validStructure: l.valid_structure, protocol, fields, error: failure?.layer === i && failure.field === null ? failure.message : null }
  })
}

// A capture's decoded tree as layers, for the viewer: no document and no definitions, so every field is read-only,
// edited as text, and never user-set.
export function layersOfTree(tree: PacketTree): CombinedLayer[] {
  const ends = layerEnds(tree)
  return tree.data.map((l, i) => {
    const end = ends[i]
    const fields = l.fields.map((f, k): CombinedField => {
      return {
        id: f.id, def: { id: f.id, name: f.id }, value: f.value, shown: String(f.hvalue ?? ''), userSet: false, ignored: f.ignored === true,
        readOnly: true, range: fieldRange(l.fields, k, l.offset, end), bits: null, error: null,
      }
    })
    return { index: i, id: l.id, realId: l.real_id, offset: l.offset, validStructure: l.valid_structure, protocol: null, fields, error: null }
  })
}

// The narrowest field whose bytes hold byte `i`, with its layer; null when no field does. The same rule selects a field
// from a byte in the capture viewer and finds the field a byte edit belongs to.
export function fieldAt(layers: CombinedLayer[], i: number): { layer: number; field: string; range: Range } | null {
  let best: { layer: number; field: string; range: Range } | null = null
  for (const l of layers) for (const f of l.fields) {
    const r = f.range
    if (r && i >= r[0] && i < r[1] && (best === null || r[1] - r[0] < best.range[1] - best.range[0])) best = { layer: l.index, field: f.id, range: r }
  }
  return best
}
