import { describe, expect, it } from 'vitest'
import type { Tunable } from './api'
import { formMult } from './rateText'
import { defaultsFor, parseMult, tunablesToArgs, type MultUnit } from './traffic'

const T = (p: Partial<Tunable>): Tunable => ({ dest: 'x', flag: '--x', kind: 'str', default: null, choices: null, help: '', ...p })

describe('tunablesToArgs', () => {
  const specs = [T({ dest: 'packet_len', flag: '--packet_len', kind: 'int', default: 64 }),
                 T({ dest: 'fast', flag: '--fast', kind: 'bool_true', default: false }),
                 T({ dest: 'mode', flag: '--mode', kind: 'choice', choices: ['a', 'b'], default: 'a' })]
  it('omits values equal to the default', () => { expect(tunablesToArgs(specs, defaultsFor(specs), '')).toEqual([]) })
  it('emits changed values and flags', () => {
    expect(tunablesToArgs(specs, { packet_len: 128, fast: true, mode: 'b' }, '')).toEqual(['--packet_len', '128', '--fast', '--mode', 'b'])
  })
  it('appends extra args split on whitespace', () => { expect(tunablesToArgs(specs, defaultsFor(specs), ' --y 1  --z ')).toEqual(['--y', '1', '--z']) })
  it('bool_false emits its flag when switched off from a true default', () => {
    const s = [T({ dest: 'k', flag: '--no-k', kind: 'bool_false', default: true })]
    expect(tunablesToArgs(s, { k: false }, '')).toEqual(['--no-k'])
    expect(tunablesToArgs(s, { k: true }, '')).toEqual([])
  })
  it('raw splits on whitespace, and a positional raw spec emits no flag', () => {
    const s = [T({ dest: 'r', flag: '--r', kind: 'raw', default: '' }), T({ dest: 'p', flag: '', kind: 'raw', default: '' })]
    expect(tunablesToArgs(s, { r: 'a  b', p: 'c d' }, '')).toEqual(['--r', 'a', 'b', 'c', 'd'])
    expect(tunablesToArgs(s, { r: '', p: '' }, '')).toEqual([])
  })
  it('treats a form string equal to the default as unchanged', () => {
    expect(tunablesToArgs(specs, { packet_len: '64' }, '')).toEqual([])
    expect(tunablesToArgs(specs, { packet_len: '65' }, '')).toEqual(['--packet_len', '65'])
  })
  it('skips a cleared numeric field instead of sending "NaN" or an empty value', () => {
    expect(tunablesToArgs(specs, { packet_len: '' }, '')).toEqual([])
  })
})

describe('parseMult', () => {
  it('parseMult inverts formMult for every unit', () => {
    const units: MultUnit[] = ['x', '%', 'pps', 'kpps', 'mpps', 'bps', 'kbps', 'mbps', 'gbps', 'bpsl1', 'kbpsl1', 'mbpsl1', 'gbpsl1']
    for (const u of units) expect(parseMult(formMult('2.5', u) as string)).toEqual({ value: '2.5', unit: u })
    expect(parseMult(formMult('100', '%') as string)).toEqual({ value: '100', unit: '%' })
  })
  it('refuses what the form cannot show', () => {
    for (const m of ['', 'fast', '10tpps', '1e3', '-5', '10kpps+']) expect(parseMult(m)).toBeNull()
  })
})
