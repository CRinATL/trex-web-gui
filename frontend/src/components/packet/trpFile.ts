import type { DocLayer, FeInstruction, FeValue, PacketDoc } from '../../api'

// A .trp file is the packet document as JSON (D5). Legacy files spell the global Field Engine parameters `fePrarameters`;
// the backend also takes `feParameters`. A file is read under either spelling and always written under the legacy one.
export const FILE_TYPE = 'trex-packet-editor'
export const VERSION = '1.0.0'
export class TrpError extends Error {}

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)

// A new document: one Ether layer and the default cache size (the backend's packetdoc.empty_doc).
export const newDoc = (): PacketDoc => ({
  fileType: FILE_TYPE, version: VERSION, metadata: { caption: '' },
  packet: [{ id: 'Ether', fields: [] }], fePrarameters: { cache_size: '1000' }, feInstructions: [],
})

function layersOf(raw: unknown): DocLayer[] {
  if (!Array.isArray(raw) || raw.length === 0) throw new TrpError('"packet" must be a list with at least one layer')
  return raw.map((l: unknown, i) => {
    if (!isObject(l) || typeof l.id !== 'string' || l.id === '') throw new TrpError(`layer ${i} has no id`)
    if (l.fields !== undefined && !Array.isArray(l.fields)) throw new TrpError(`layer ${i}: "fields" must be a list`)
    return { ...l, id: l.id, fields: (l.fields ?? []) as DocLayer['fields'] }
  })
}

// The document in `text`. Structure only: the values are the backend's to judge when the packet is built.
export function readTrp(text: string): PacketDoc {
  let raw: unknown
  try { raw = JSON.parse(text) } catch (e) { throw new TrpError(`not JSON: ${e instanceof Error ? e.message : String(e)}`) }
  if (!isObject(raw)) throw new TrpError('expected a JSON object')
  if (raw.fileType !== FILE_TYPE) throw new TrpError(`not a packet editor file (fileType is not ${FILE_TYPE})`)
  if ('fePrarameters' in raw && 'feParameters' in raw) throw new TrpError('give fePrarameters or feParameters, not both')
  const params = 'fePrarameters' in raw ? raw.fePrarameters : raw.feParameters
  if (params !== undefined && !isObject(params)) throw new TrpError('the Field Engine parameters must be an object')
  if (raw.feInstructions !== undefined && !Array.isArray(raw.feInstructions)) throw new TrpError('"feInstructions" must be a list')
  const caption = isObject(raw.metadata) && typeof raw.metadata.caption === 'string' ? raw.metadata.caption : ''
  return {
    fileType: FILE_TYPE, version: typeof raw.version === 'string' ? raw.version : VERSION, metadata: { caption },
    packet: layersOf(raw.packet),
    fePrarameters: (params ?? {}) as Record<string, FeValue>, feInstructions: (raw.feInstructions ?? []) as FeInstruction[],
  }
}

// The file's text, in the legacy key order and spelling.
export const writeTrp = (doc: PacketDoc): string => JSON.stringify({
  fileType: doc.fileType, version: doc.version, metadata: doc.metadata, packet: doc.packet,
  fePrarameters: doc.fePrarameters, feInstructions: doc.feInstructions,
})
