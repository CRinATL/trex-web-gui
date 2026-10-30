// The Field Engine's model, pure (no React): edits to a document's instructions and global parameters. Every parameter
// value is stored as a string (or a vtype object from an import): the service lowercases a string value and fails on a
// number (opus M5).
import type { Definitions, FeInstruction, FeParamMeta, FeValue, PacketDoc } from '../../api'

// The parameters whose values the service lists from the packet and the instructions (scapy_service.py:901-920).
export const ENUM_PARAMS = ['name', 'fv_name', 'pkt_offset', 'offset']

export type FeMetas = Pick<Definitions, 'feInstructions' | 'feInstructionParameters'>

const paramMeta = (metas: FeMetas, id: string): FeParamMeta | undefined => metas.feInstructionParameters.find((p) => p.id === id)
const defaultOf = (m: FeParamMeta | undefined): string => (m?.defaultValue === undefined ? '' : String(m.defaultValue))

// An instruction of id `id`, each parameter at its meta's default (as a string), or "" when the meta has none.
function newInstruction(id: string, metas: FeMetas, over: Record<string, string> = {}): FeInstruction | null {
  const meta = metas.feInstructions.find((m) => m.id === id)
  if (!meta) return null
  const parameters: Record<string, FeValue> = {}
  for (const p of meta.parameters) parameters[p] = over[p] ?? defaultOf(paramMeta(metas, p))
  return { id, parameters }
}

// Append an instruction; an unknown id changes nothing.
export function addInstruction(doc: PacketDoc, id: string, metas: FeMetas): PacketDoc {
  const ins = newInstruction(id, metas)
  return ins ? { ...doc, feInstructions: [...doc.feInstructions, ins] } : doc
}

export function deleteInstruction(doc: PacketDoc, index: number): PacketDoc {
  if (index < 0 || index >= doc.feInstructions.length) return doc
  return { ...doc, feInstructions: doc.feInstructions.filter((_, i) => i !== index) }
}

export function setParam(doc: PacketDoc, index: number, paramId: string, value: string): PacketDoc {
  const ins = doc.feInstructions[index]
  if (!ins || ins.parameters[paramId] === value) return doc
  return { ...doc, feInstructions: doc.feInstructions.map((x, i) => (i === index ? { ...x, parameters: { ...x.parameters, [paramId]: value } } : x)) }
}

// Set a global parameter; an empty value drops it, so the service's default applies.
export function setGlobal(doc: PacketDoc, id: string, value: string): PacketDoc {
  const cur = doc.fePrarameters
  if (value === '') {
    if (!(id in cur)) return doc
    return { ...doc, fePrarameters: Object.fromEntries(Object.entries(cur).filter(([k]) => k !== id)) }
  }
  return cur[id] === value ? doc : { ...doc, fePrarameters: { ...cur, [id]: value } }
}

// A field-engine template on one field: the variable is named `<layer>_<field>` and written at `<layer>.<field>` (the
// service's own spelling; a layer id that occurs more than once is `<layer>:<n>`). Other parameters are the meta defaults (B2.6).
export function fromTemplate(doc: PacketDoc, templateId: string, layerId: string, fieldId: string, defs: Definitions, layerIndex?: number): PacketDoc {
  const tpl = defs.feTemplates.find((t) => t.id === templateId)
  if (!tpl) return doc
  const same = doc.packet.filter((l) => l.id === layerId).length
  const nth = layerIndex === undefined ? 0 : doc.packet.slice(0, layerIndex).filter((l) => l.id === layerId).length
  const name = `${layerId}_${fieldId}`
  const offset = `${same > 1 ? `${layerId}:${nth}` : layerId}.${fieldId}`
  const made: FeInstruction[] = []
  for (const id of tpl.instructionIds) {
    const ins = newInstruction(id, defs, { name, fv_name: name, pkt_offset: offset })
    if (ins) made.push(ins)
  }
  return made.length === 0 ? doc : { ...doc, feInstructions: [...doc.feInstructions, ...made] }
}

// The text a parameter value shows: a string as it is, a vtype object by its expression.
export const feText = (v: FeValue | undefined): string => (v === undefined ? '' : typeof v === 'string' ? v : (v.expr ?? JSON.stringify(v)))
