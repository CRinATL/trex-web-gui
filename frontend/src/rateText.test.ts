import { describe, expect, it } from 'vitest'
import { formatRate, formMult, fromWireMult, parseRate, readingOf, toWireMult, type ParsedRate } from './rateText'
import { MULT_RE } from './traffic'

const STUDIO_COPY = (t: string) => `Can't read "${t}": a number, optionally with k/M/G and pps, bps or %.`
const CONTROL_COPY = (t: string) => `Can't read "${t}": a number, optionally with k/M/G and pps, bps or %, or × for a factor.`

describe('parseRate', () => {
  it.each<[string, 'pps' | 'factor', ParsedRate]>([
    ['1000', 'pps', { kind: 'pps', value: 1000 }],
    ['0.5', 'pps', { kind: 'pps', value: 0.5 }],
    ['.5', 'pps', { kind: 'pps', value: 0.5 }],
    ['1.5 Mpps', 'pps', { kind: 'pps', value: 1.5e6 }],
    ['200kpps', 'pps', { kind: 'pps', value: 2e5 }],
    ['10 Gbps', 'pps', { kind: 'bps_L2', value: 1e10 }],
    ['512k bps', 'pps', { kind: 'bps_L2', value: 512e3 }],
    ['10 Gbps L1', 'pps', { kind: 'bps_L1', value: 1e10 }],
    ['10gbpsl1', 'pps', { kind: 'bps_L1', value: 1e10 }],
    ['25%', 'pps', { kind: 'percentage', value: 25 }],
    ['2.5 %', 'pps', { kind: 'percentage', value: 2.5 }],
    ['1000', 'factor', { kind: 'factor', value: 1000 }],
    ['x2', 'factor', { kind: 'factor', value: 2 }],
    ['2×', 'factor', { kind: 'factor', value: 2 }],
    ['10 MBPS', 'pps', { kind: 'bps_L2', value: 1e7 }],
  ])('%s (bare %s)', (text, bare, want) => {
    expect(parseRate(text, bare)).toEqual(want)
  })

  it.each<[string, 'pps' | 'factor', string]>([
    ['fast', 'pps', STUDIO_COPY('fast')],
    ['fast', 'factor', CONTROL_COPY('fast')],
    ['0', 'pps', 'Enter a rate above 0.'],
    ['-1', 'pps', 'Enter a rate above 0.'],
    ['', 'pps', 'Enter a rate above 0.'],
    ['150%', 'pps', 'A percentage is at most 100.'],
    ['x2', 'pps', STUDIO_COPY('x2')],
    ['10 L1', 'pps', STUDIO_COPY('10 L1')],
    ['200k', 'pps', STUDIO_COPY('200k')],
    ['10 M', 'factor', CONTROL_COPY('10 M')],
  ])('error: %s (bare %s)', (text, bare, error) => {
    expect(parseRate(text, bare)).toEqual({ error })
  })
})

describe('formatRate', () => {
  const rates: ParsedRate[] = [
    { kind: 'pps', value: 1000 }, { kind: 'pps', value: 0.5 }, { kind: 'pps', value: 999 },
    { kind: 'pps', value: 1234567 }, { kind: 'pps', value: 1500 }, { kind: 'pps', value: 1.5e6 },
    { kind: 'bps_L2', value: 1e10 }, { kind: 'bps_L2', value: 512e3 }, { kind: 'bps_L1', value: 2.5e9 },
    { kind: 'percentage', value: 25 }, { kind: 'percentage', value: 2.5 },
    { kind: 'factor', value: 0.5 }, { kind: 'factor', value: 1000 },
  ]
  it.each(rates)('round trips %j', (r) => {
    const p = parseRate(formatRate(r), r.kind === 'factor' ? 'factor' : 'pps')
    expect('error' in p).toBe(false)
    expect(p).toEqual(r)
  })
  it('prints the box text', () => {
    expect(formatRate({ kind: 'pps', value: 1500 })).toBe('1.5 kpps')
    expect(formatRate({ kind: 'pps', value: 1234567 })).toBe('1.234567 Mpps')
    expect(formatRate({ kind: 'pps', value: 0.5 })).toBe('0.5 pps')
    expect(formatRate({ kind: 'pps', value: 999 })).toBe('999 pps')
    expect(formatRate({ kind: 'pps', value: 1500000 })).toBe('1.5 Mpps')
    expect(formatRate({ kind: 'bps_L2', value: 1e10 })).toBe('10 Gbps')
    expect(formatRate({ kind: 'bps_L1', value: 1e10 })).toBe('10 Gbps L1')
    expect(formatRate({ kind: 'percentage', value: 25 })).toBe('25%')
    expect(formatRate({ kind: 'factor', value: 0.5 })).toBe('×0.5')
  })
})

describe('readingOf', () => {
  it('reads each kind', () => {
    expect(readingOf({ kind: 'pps', value: 1.5e6 })).toBe('= 1,500,000 pps')
    expect(readingOf({ kind: 'bps_L2', value: 1e10 })).toBe('= 10 Gbit/s (L2)')
    expect(readingOf({ kind: 'bps_L1', value: 1e10 })).toBe('= 10 Gbit/s (L1)')
    expect(readingOf({ kind: 'percentage', value: 25 })).toBe('= 25 % of line rate')
    expect(readingOf({ kind: 'factor', value: 0.5 })).toBe("= ×0.5 the profile's rates")
  })
})

describe('wire multiplier', () => {
  it('writes and reads TRex text', () => {
    expect(toWireMult({ kind: 'bps_L2', value: 2.5e9 })).toBe('2.5gbps')
    expect(toWireMult({ kind: 'pps', value: 1e10 })).toBe('10gpps')
    expect(toWireMult({ kind: 'bps_L1', value: 1.5e6 })).toBe('1.5mbpsl1')
    expect(toWireMult({ kind: 'percentage', value: 25 })).toBe('25%')
    expect(toWireMult({ kind: 'factor', value: 0.5 })).toBe('0.5')
    expect(fromWireMult('2.5gbps')).toEqual({ kind: 'bps_L2', value: 2.5e9 })
    expect(fromWireMult('3')).toEqual({ kind: 'factor', value: 3 })
    expect(fromWireMult('1.5mbpsl1')).toEqual({ kind: 'bps_L1', value: 1.5e6 })
    expect(fromWireMult('25%')).toEqual({ kind: 'percentage', value: 25 })
    expect(fromWireMult('nonsense')).toBeNull()
  })
  it('is null when the text has an exponent', () => {
    expect(toWireMult({ kind: 'pps', value: 1e-7 })).toBeNull()
    expect(toWireMult({ kind: 'factor', value: 1e21 })).toBeNull()
  })
})

describe('formMult', () => {
  it.each([
    ['50', '%', '50%'], ['10', 'kpps', '10kpps'], ['1.5', 'gbps', '1.5gbps'], ['2', 'mbpsl1', '2mbpsl1'], ['3', 'x', '3'],
    ['10.0', 'kpps', '10kpps'], ['1500', 'kpps', '1.5mpps'],
  ] as const)('%s %s → %s', (v, u, want) => { expect(formMult(v, u)).toBe(want); expect(MULT_RE.test(want)).toBe(true) })
  it('sends every digit the box reads', () => {
    expect(formMult('14.880952', 'mpps')).toBe('14.880952mpps')
    expect(formMult('1.23456789', 'pps')).toBe('1.23456789pps')
    expect(formMult('0.000001', 'x')).toBe('0.000001')
  })
  it('refuses zero, an empty or unreadable value and a value TRex would read as an exponent', () => {
    for (const v of ['0', '0.0', '', ' ', 'fast', '-1', '1e3']) expect(formMult(v, 'pps')).toBeNull()
    expect(formMult('0.0000001', 'x')).toBeNull()
  })
})
