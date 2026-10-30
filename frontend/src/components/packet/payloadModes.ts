import type { FieldValue, VtypeValue } from '../../api'
import { decodeBase64, encodeBase64 } from './hexModel'

// The seven ways a BYTES field is given (generate_bytes, scapy_service.py:411-431).
export type PayloadMode = 'text' | 'file' | 'text_pattern' | 'file_pattern' | 'code_pattern' | 'random_ascii' | 'random_bytes'
// "load" sets the payload's own length (`size`); "packet" the whole frame with FCS (`total_size`), from which the service
// subtracts the headers and 4 bytes (scapy_service.py:615-622).
export type SizeKind = 'load' | 'packet'

export const SIZE_MAX = 1 << 20          // verify_payload_size (scapy_service.py:406-409)
export const DEFAULT_SEED = 12345        // `int(seed or 12345)` (scapy_service.py:426)

export const MODES: { id: PayloadMode; label: string }[] = [
  { id: 'text', label: 'Text' },
  { id: 'file', label: 'File' },
  { id: 'text_pattern', label: 'Text pattern' },
  { id: 'file_pattern', label: 'File pattern' },
  { id: 'code_pattern', label: 'Hex bytes pattern' },
  { id: 'random_ascii', label: 'Random ASCII' },
  { id: 'random_bytes', label: 'Random non-ASCII' },
]

export interface PayloadInput {
  text: string                  // text and text pattern; the hex text of the code pattern
  bytes: Uint8Array | null      // the file's content, for the file modes
  sizeKind: SizeKind
  size: number | null           // null: not given (the code pattern then uses its own length)
  seed: number
}
export const emptyInput = (): PayloadInput => ({ text: '', bytes: null, sizeKind: 'load', size: null, seed: DEFAULT_SEED })

// Why an input cannot become a value; the message is the one the dialog shows.
export class PayloadError extends Error {}

const utf8 = (text: string) => new TextEncoder().encode(text)

function sizeKey(input: PayloadInput, required: boolean): { size?: number; total_size?: number } {
  if (input.size === null) {
    if (required) throw new PayloadError('enter a size')
    return {}
  }
  if (!Number.isInteger(input.size) || input.size < 1 || input.size > SIZE_MAX) throw new PayloadError(`the size must be a whole number from 1 to ${SIZE_MAX}`)
  return input.sizeKind === 'packet' ? { total_size: input.size } : { size: input.size }
}

// The hex digits of a code pattern, as parse_template_code reads them (scapy_service.py:401-404): "0x" and spaces dropped.
const hexDigits = (text: string) => text.replace(/0[xX]/g, '').replace(/\s/g, '')

export function toValue(mode: PayloadMode, input: PayloadInput): FieldValue {
  const bytesOf = (): Uint8Array => {
    if (input.bytes === null) throw new PayloadError('choose a file')
    return input.bytes
  }
  switch (mode) {
    case 'text': return { vtype: 'BYTES', base64: encodeBase64(utf8(input.text)) }
    case 'file': return { vtype: 'BYTES', base64: encodeBase64(bytesOf()) }
    case 'text_pattern':
    case 'file_pattern': {
      const template = mode === 'text_pattern' ? utf8(input.text) : bytesOf()
      if (template.length === 0) throw new PayloadError('the pattern is empty')
      return { vtype: 'BYTES', generate: 'template', template_base64: encodeBase64(template), ...sizeKey(input, true) }
    }
    case 'code_pattern': {
      const digits = hexDigits(input.text)
      if (digits === '') throw new PayloadError('enter hex bytes')
      if (!/^[\da-f]*$/i.test(digits) || digits.length % 2 !== 0) throw new PayloadError('hex bytes are pairs of hex digits, for example 0a 1b 2c')
      return { vtype: 'BYTES', generate: 'template_code', template_code: input.text.trim(), ...sizeKey(input, false) }
    }
    case 'random_ascii':
    case 'random_bytes':
      if (!Number.isSafeInteger(input.seed)) throw new PayloadError('the seed must be a whole number')
      if (input.seed === 0) throw new PayloadError(`the service reads seed 0 as ${DEFAULT_SEED}; use another seed`)
      return { vtype: 'BYTES', generate: mode, seed: input.seed, ...sizeKey(input, true) }
  }
}

// UTF-8 text from base64, or null when the bytes are not text a textarea keeps byte for byte (they then belong to a file
// mode). A textarea turns CR LF and CR into LF and cannot show other control bytes, so any C0 byte but tab and line feed,
// and DEL, sends the payload to the file modes.
const notTextareaSafe = (text: string) => Array.from(text).some((c) => { const n = c.charCodeAt(0); return (n < 0x20 && n !== 0x0a && n !== 0x09) || n === 0x7f })
function textOfBase64(b64: string): { text: string; bytes: Uint8Array } | null {
  try {
    const bytes = decodeBase64(b64)
    const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes)
    return notTextareaSafe(text) ? null : { text, bytes }
  } catch { return null }
}
// What the dialog shows for a stored value: the mode and the input that make it again. A value that is not a BYTES
// object (a field not yet given) opens as empty text.
export function fromValue(v: FieldValue | undefined): { mode: PayloadMode; input: PayloadInput; note?: string } {
  const input = emptyInput()
  if (typeof v !== 'object' || v === null || Array.isArray(v) || v.vtype !== 'BYTES') return { mode: 'text', input }
  const b = v as VtypeValue
  const sized = () => {
    if (b.total_size !== undefined) { input.sizeKind = 'packet'; input.size = b.total_size }
    else if (b.size !== undefined) input.size = b.size
  }
  if (b.generate === undefined) {
    const t = textOfBase64(b.base64 ?? '')
    if (t) { input.text = t.text; return { mode: 'text', input } }
    input.bytes = decodeBase64(b.base64 ?? '')
    return { mode: 'file', input }
  }
  sized()
  if (b.generate === 'template') {
    const t = textOfBase64(b.template_base64 ?? '')
    if (t) { input.text = t.text; return { mode: 'text_pattern', input } }
    input.bytes = decodeBase64(b.template_base64 ?? '')
    return { mode: 'file_pattern', input }
  }
  if (b.generate === 'template_code') { input.text = b.template_code ?? ''; return { mode: 'code_pattern', input } }
  if (b.generate !== 'random_ascii' && b.generate !== 'random_bytes') {
    return { mode: 'text', input: emptyInput(), note: `This field's generator "${String(b.generate)}" is not one this editor knows; saving replaces it.` }
  }
  input.seed = b.seed ?? DEFAULT_SEED
  return { mode: b.generate, input }
}
