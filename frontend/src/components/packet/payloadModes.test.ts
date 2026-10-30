import { describe, expect, it } from 'vitest'
import type { FieldValue } from '../../api'
import { emptyInput, fromValue, PayloadError, toValue, type PayloadInput, type PayloadMode } from './payloadModes'

const b64 = (s: string) => btoa(s)
const input = (over: Partial<PayloadInput> = {}): PayloadInput => ({ ...emptyInput(), ...over })
const bin = new Uint8Array([0, 1, 2, 255, 128])   // not UTF-8

describe('payloadModes toValue', () => {
  it('text', () => {
    expect(toValue('text', input({ text: 'héllo' }))).toEqual({ vtype: 'BYTES', base64: b64('h\xc3\xa9llo') })
  })
  it('file', () => {
    expect(toValue('file', input({ bytes: bin }))).toEqual({ vtype: 'BYTES', base64: 'AAEC/4A=' })
  })
  it('text pattern', () => {
    expect(toValue('text_pattern', input({ text: 'ab', size: 7 }))).toEqual({ vtype: 'BYTES', generate: 'template', template_base64: b64('ab'), size: 7 })
  })
  it('text pattern with a packet size sends total_size', () => {
    expect(toValue('text_pattern', input({ text: 'ab', size: 64, sizeKind: 'packet' }))).toEqual({ vtype: 'BYTES', generate: 'template', template_base64: b64('ab'), total_size: 64 })
  })
  it('file pattern', () => {
    expect(toValue('file_pattern', input({ bytes: bin, size: 100 }))).toEqual({ vtype: 'BYTES', generate: 'template', template_base64: 'AAEC/4A=', size: 100 })
  })
  it('code pattern', () => {
    expect(toValue('code_pattern', input({ text: ' 0x0a 1B ', size: 20 }))).toEqual({ vtype: 'BYTES', generate: 'template_code', template_code: '0x0a 1B', size: 20 })
  })
  it('code pattern without size has no size key', () => {
    const v = toValue('code_pattern', input({ text: '0a1b' })) as Record<string, unknown>
    expect(v).toEqual({ vtype: 'BYTES', generate: 'template_code', template_code: '0a1b' })
    expect('size' in v || 'total_size' in v).toBe(false)
  })
  it('code pattern with a packet size sends total_size', () => {
    expect(toValue('code_pattern', input({ text: '0a', size: 60, sizeKind: 'packet' }))).toEqual({ vtype: 'BYTES', generate: 'template_code', template_code: '0a', total_size: 60 })
  })
  it('random ASCII defaults the seed to 12345', () => {
    expect(toValue('random_ascii', input({ size: 32 }))).toEqual({ vtype: 'BYTES', generate: 'random_ascii', seed: 12345, size: 32 })
  })
  it('random non-ASCII', () => {
    expect(toValue('random_bytes', input({ size: 32, seed: 7, sizeKind: 'packet' }))).toEqual({ vtype: 'BYTES', generate: 'random_bytes', seed: 7, total_size: 32 })
  })
  it('size 1048577 refused', () => {
    expect(() => toValue('random_ascii', input({ size: 1048577 }))).toThrow(PayloadError)
    expect(toValue('random_ascii', input({ size: 1048576 }))).toMatchObject({ size: 1048576 })
  })
  it('refuses a missing, zero, negative or fractional size', () => {
    for (const size of [null, 0, -1, 1.5]) expect(() => toValue('text_pattern', input({ text: 'a', size }))).toThrow(PayloadError)
  })
  it('refuses an empty pattern, a file mode without a file, and bad hex', () => {
    expect(() => toValue('text_pattern', input({ text: '', size: 4 }))).toThrow('empty')
    expect(() => toValue('file', input())).toThrow('choose a file')
    expect(() => toValue('code_pattern', input({ text: '0a1' }))).toThrow(PayloadError)
    expect(() => toValue('code_pattern', input({ text: 'zz' }))).toThrow(PayloadError)
    expect(() => toValue('code_pattern', input({ text: '' }))).toThrow(PayloadError)
  })
})

describe('payloadModes fromValue', () => {
  const cases: [PayloadMode, PayloadInput][] = [
    ['text', input({ text: 'héllo' })],
    ['file', input({ bytes: bin })],
    ['text_pattern', input({ text: 'ab', size: 7 })],
    ['file_pattern', input({ bytes: bin, size: 100 })],
    ['code_pattern', input({ text: '0a 1b', size: 9 })],
    ['code_pattern', input({ text: '0a 1b' })],
    ['random_ascii', input({ size: 32, seed: 5 })],
    ['random_bytes', input({ size: 64, sizeKind: 'packet' })],
  ]
  it('fromValue inverts toValue for every mode', () => {
    for (const [mode, inp] of cases) {
      const v = toValue(mode, inp)
      const back = fromValue(v)
      expect(back.mode).toBe(mode)
      expect(toValue(back.mode, back.input)).toEqual(v)
    }
  })
  it('a value that is not a BYTES object opens as empty text', () => {
    for (const v of [undefined, null, 'x', 5, { vtype: 'EXPRESSION', expr: '1' }] as (FieldValue | undefined)[]) expect(fromValue(v)).toEqual({ mode: 'text', input: emptyInput() })
  })
  it('payloads a textarea would change open in a file mode', () => {
    for (const raw of ['a\r\nb', 'a\rb', 'a\x00b', 'a\x1bb']) {
      expect(fromValue({ vtype: 'BYTES', base64: btoa(raw) }).mode).toBe('file')
      expect(fromValue({ vtype: 'BYTES', generate: 'template', template_base64: btoa(raw), size: 4 }).mode).toBe('file_pattern')
    }
    expect(fromValue({ vtype: 'BYTES', base64: btoa('a\nb\tc') }).mode).toBe('text')
  })
  it('seed 0 refused', () => {
    expect(() => toValue('random_ascii', input({ size: 4, seed: 0 }))).toThrow('seed 0')
  })
  it('an unknown generate falls back to text with a note', () => {
    const r = fromValue({ vtype: 'BYTES', generate: 'nope' as never })
    expect(r.mode).toBe('text')
    expect(r.note).toContain('nope')
  })
  it('a total_size reads back as a packet size', () => {
    expect(fromValue({ vtype: 'BYTES', generate: 'random_ascii', seed: 1, total_size: 80 }).input).toMatchObject({ sizeKind: 'packet', size: 80 })
  })
})
