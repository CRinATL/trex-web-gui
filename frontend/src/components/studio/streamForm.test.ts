import { describe, expect, it } from 'vitest'
import type { StreamJson, StreamPage } from '../../api'
import streams from '../../__fixtures__/studioStreams.json'
import { formErrors, nextChoices, patchFrom, sentFields, toForm, type StreamFormValues } from './streamForm'

const recorded = Object.values(streams as unknown as Record<string, StreamPage>).flatMap((p) => p.rows.map((r) => r.stream))
const base = recorded[0]
const form = (over: Partial<StreamFormValues> = {}, s: StreamJson = base): StreamFormValues => ({ ...toForm(s), ...over })
const burst: StreamJson = { ...base, name: 'b', mode: { type: 'multi_burst', rate: { type: 'pps', value: 10 }, pkts_per_burst: 5, ibg: 100, count: 3 } }

describe('streamForm', () => {
  it('a recorded stream round-trips through the form with an empty patch', () => {
    expect(recorded.length).toBeGreaterThan(0)
    for (const s of [...recorded, burst]) expect(patchFrom(s, toForm(s))).toEqual({})
  })

  it('burst counts below 1 are errors', () => {
    const f = form({ mode: 'multi_burst', pktsPerBurst: '0', count: '0', totalPkts: '0' })
    expect(Object.keys(formErrors(f, [null], undefined))).toEqual(expect.arrayContaining(['pktsPerBurst', 'count']))
    expect(formErrors(form({ mode: 'single_burst', totalPkts: '0' }), [null], undefined).totalPkts).toBeTruthy()
    expect(formErrors(form({ mode: 'single_burst', totalPkts: '1.5' }), [null], undefined).totalPkts).toBeTruthy()
    expect(formErrors(form({ mode: 'single_burst', totalPkts: '3' }), [null], undefined)).toEqual({})
    // the counts of a mode that is not selected are not checked
    expect(formErrors(form({ mode: 'continuous', totalPkts: '0', count: '0' }), [null], undefined)).toEqual({})
  })

  it('percentage over 100 is an error', () => {
    expect(formErrors(form({ rate: '100.5%' }), [null], undefined).rate).toBeTruthy()
    expect(formErrors(form({ rate: '100%' }), [null], undefined)).toEqual({})
    expect(formErrors(form({ rate: '1000000' }), [null], undefined)).toEqual({})
    for (const bad of ['0', '-1', '', 'abc', 'Infinity']) expect(formErrors(form({ rate: bad }), [null], undefined).rate).toBeTruthy()
  })

  it('the rate is one typed text', () => {
    const at = (rate: StreamJson['mode']['rate']): StreamJson => ({ ...base, mode: { type: 'continuous', rate } })
    expect(toForm(at({ type: 'pps', value: 1500000 })).rate).toBe('1.5 Mpps')
    expect(formErrors(form({ rate: 'fast' }), [null], undefined).rate).toBe(`Can't read "fast": a number, optionally with k/M/G and pps, bps or %.`)
    expect(patchFrom(base, form({ rate: '25%' })).mode).toEqual({ type: 'continuous', rate: { type: 'percentage', value: 25 } })
  })

  it('an untouched rate is not a change', () => {
    for (const rate of [{ type: 'pps', value: 1500000 }, { type: 'pps', value: 0.5 }, { type: 'bps_L1', value: 12345678 }, { type: 'pps', value: 14880952.380952381 }, { type: 'pps', value: 10000 / 3 }] as const) {
      const s: StreamJson = { ...base, mode: { type: 'continuous', rate } }
      expect(patchFrom(s, toForm(s))).toEqual({})
    }
  })

  it('a stored rate that does not re-parse exactly survives an unrelated edit', () => {
    for (const value of [14880952.380952381, 10000 / 3, 1e-7]) {
      const s: StreamJson = { ...base, mode: { type: 'continuous', rate: { type: 'pps', value } } }
      const f = { ...toForm(s), isg: '77' }
      expect(formErrors(f, [null], undefined, undefined, toForm(s).rate)).toEqual({})
      expect(patchFrom(s, f)).toEqual({ isg: 77 })
    }
    expect(formErrors(form({ rate: 'fast' }), [null], undefined, undefined, toForm(base).rate).rate).toBeTruthy()
  })

  it('a name with a quote is an error', () => {
    for (const bad of ["a'b", 'a"b', 'a\\b', 'café', 'x'.repeat(81)]) expect(formErrors(form({ name: bad }), [null], undefined).name).toBeTruthy()
    expect(formErrors(form({ name: 'ok name' }), [null], undefined)).toEqual({})
  })

  it('the shared name table', () => {      // the rows of tests/unit/test_validation.py NAME_ACCEPT and NAME_REFUSE
    for (const ok of ['udp_1', 'a b', 'x'.repeat(80), '#&()[]~']) expect(formErrors(form({ name: ok }), [null], undefined).name).toBeUndefined()
    for (const bad of ['x'.repeat(81), "a'b", 'a"b', 'a\\b', 'a\tb', 'a\nb', 'café']) expect(formErrors(form({ name: bad }), [null], undefined).name).toBeTruthy()
    expect(formErrors(form({ name: '' }), [null], undefined).name).toBeUndefined()    // empty removes the name
  })

  it('a name taken by another stream is an error, by value and type', () => {
    expect(formErrors(form({ name: 'a' }), ['a', 'b'], 'b').name).toMatch(/taken/)
    expect(formErrors(form({ name: 'b' }), ['a', 'b'], 'b').name).toBeUndefined()
    expect(formErrors(form({ name: '1' }), [1, 'x'], 'x').name).toBeUndefined()      // 1 and "1" differ
    expect(formErrors(form({ name: '1' }), [1, 'x'], 1).name).toBeUndefined()        // the stream's own int name, unchanged
  })

  it('next choices exclude self and unnamed streams', () => {
    expect(nextChoices(['a', null, 'b', 3], 'b')).toEqual(['a', 3])
    expect(nextChoices(['a', null], undefined)).toEqual(['a'])
    const names = ['a', 'b']
    expect(formErrors(form({ next: JSON.stringify('b') }), names, 'b').next).toBeTruthy()
    expect(formErrors(form({ next: JSON.stringify('zzz') }), names, 'b').next).toBeTruthy()
    expect(formErrors(form({ next: JSON.stringify('a') }), names, 'b')).toEqual({})
  })

  it('next patches by name and type, and Stop removes it', () => {
    const s: StreamJson = { ...burst, next: 2 }
    expect(patchFrom(s, form({ next: '' }, s))).toEqual({ next: null })
    expect(patchFrom(burst, form({ next: JSON.stringify('b2') }, burst))).toEqual({ next: 'b2' })
    expect(patchFrom(burst, form({ next: JSON.stringify(7) }, burst))).toEqual({ next: 7 })
    // switching to continuous drops the next
    expect(patchFrom(s, form({ mode: 'continuous' }, s)).next).toBeNull()
  })

  it('random seed 0 patches null', () => {
    expect(patchFrom({ ...base, random_seed: 5 }, form({ randomSeed: '0' }, { ...base, random_seed: 5 }))).toEqual({ random_seed: null })
    expect(patchFrom(base, form({ randomSeed: '7' }))).toEqual({ random_seed: 7 })
    expect(patchFrom(base, form({ randomSeed: '' }))).toEqual({})
    expect(patchFrom({ ...base, random_seed: 0 }, toForm({ ...base, random_seed: 0 }))).toEqual({})
    expect(formErrors(form({ randomSeed: '4294967296' }), [null], undefined).randomSeed).toBeTruthy()
  })

  it('cache Off drops vm.cache and N sets it', () => {
    const cached: StreamJson = { ...base, vm: { instructions: [{ type: 'flow_var' }], cache: 5000 } }
    expect(patchFrom(cached, form({ cacheOn: false }, cached))).toEqual({ vm: { instructions: [{ type: 'flow_var' }] } })
    expect(patchFrom(base, form({ cacheOn: true, cacheSize: '64' }))).toEqual({ vm: { instructions: base.vm.instructions, cache: 64 } })
    expect(formErrors(form({ cacheOn: true, cacheSize: '65536' }), [null], undefined).cacheSize).toBeTruthy()
    expect(formErrors(form({ cacheOn: true, cacheSize: '0' }), [null], undefined).cacheSize).toBeTruthy()
  })

  it('latency with a core is an error', () => {
    expect(formErrors(form({ rx: 'latency', pgId: '1', coreId: '0' }), [null], undefined).coreId).toMatch(/latency/)
    expect(formErrors(form({ rx: 'latency', pgId: '1', coreId: '-1' }), [null], undefined)).toEqual({})
    expect(formErrors(form({ rx: 'stats', pgId: '1', coreId: '3' }), [null], undefined)).toEqual({})
    expect(formErrors(form({ coreId: '256' }), [null], undefined).coreId).toBeTruthy()
    expect(formErrors(form({ rx: 'stats', pgId: '' }), [null], undefined).pgId).toBeTruthy()
  })

  it('only the changed keys are patched, and packet and instructions never appear', () => {
    const p = patchFrom(base, form({ isg: '50', enabled: false, rate: '2', dummy: true, dstMac: '2', rx: 'stats', pgId: '9' }))
    expect(p).toEqual({
      isg: 50, enabled: false, mode: { type: 'continuous', rate: { type: 'pps', value: 2 } }, flags: 1 << 3 | 2 << 1,
      flow_stats: { enabled: true, rule_type: 'stats', stream_id: 9 },
    })
    expect(patchFrom(base, form({ name: 'n' }))).toEqual({ name: 'n' })
    expect(patchFrom({ ...base, name: 'n' }, form({ name: '' }, { ...base, name: 'n' }))).toEqual({ name: null })
    expect(patchFrom({ ...base, name: 1 }, toForm({ ...base, name: 1 }))).toEqual({})
    expect(Object.keys(patchFrom(base, form({ startPaused: true })))).toEqual(['start_paused'])
  })

  it('a stored next equal to the stream itself is not an error unless it is newly chosen', () => {
    const loop: StreamJson = { ...burst, name: 1, next: 1 }
    const stored = toForm(loop).next
    expect(formErrors(toForm(loop), [1], 1)).toHaveProperty('next')
    expect(formErrors(toForm(loop), [1], 1, stored)).toEqual({})
    expect(formErrors({ ...toForm(loop), isg: '3' }, [1], 1, stored)).toEqual({})
    expect(patchFrom(loop, { ...toForm(loop), isg: '3' })).toEqual({ isg: 3 })
    expect(formErrors({ ...toForm(burst), next: JSON.stringify('b') }, ['b'], 'b', '')).toHaveProperty('next')   // newly chosen self
  })

  it('fractions may be written .5 and 1.', () => {
    for (const ok of ['.5', '1.', '0.5']) expect(formErrors(form({ rate: ok, isg: ok }), [null], undefined)).toEqual({})
    expect(formErrors(form({ isg: '2e3' }), [null], undefined)).toEqual({})
    expect(formErrors(form({ rate: '2e3' }), [null], undefined).rate).toBeTruthy()      // a rate has no exponent
    for (const bad of ['.', '1..', '-.', '1.5.']) expect(formErrors(form({ isg: bad }), [null], undefined).isg).toBeTruthy()
    expect(patchFrom(base, form({ isg: '.5' }))).toEqual({ isg: 0.5 })
  })

  describe('sentFields: the form fields Save would send (R6)', () => {
    it('a form re-typed to equal values sends nothing, as patchFrom does', () => {
      const f = form({ rate: '1pps', isg: '00', actionCount: '0' + toForm(base).actionCount })
      expect(patchFrom(base, f)).toEqual({})
      expect([...sentFields(base, f)]).toEqual([])
    })
    it('names each field whose value differs', () => {
      expect([...sentFields(base, form({ rate: '2 kpps', isg: '5' }))].sort()).toEqual(['isg', 'rate'])
      expect([...sentFields(burst, form({ count: '03', ibg: '101' }, burst))]).toEqual(['ibg'])
    })
    it('a mode change names the mode, not the burst fields it shows at their defaults', () => {
      expect([...sentFields(base, form({ mode: 'single_burst' }))]).toEqual(['mode'])
    })
    it('an unreadable rate is not sent, and the untouched stored text never is', () => {
      expect(sentFields(base, form({ rate: 'fast' })).has('rate')).toBe(false)
      expect(sentFields(base, form()).has('rate')).toBe(false)
    })
    it('is empty exactly when patchFrom is, over single-field edits', () => {
      const edits: Partial<StreamFormValues>[] = [{ isg: '7' }, { isg: '0.0' }, { enabled: !toForm(base).enabled }, { randomSeed: '' }, { randomSeed: '3' },
        { coreId: '2' }, { dstMac: '2' }, { cacheOn: true }, { rx: 'stats', pgId: '4' }, { rate: '1.0 pps' }, { rate: '10 Gbps' }]
      for (const e of edits) expect(sentFields(base, form(e)).size === 0, JSON.stringify(e)).toBe(JSON.stringify(patchFrom(base, form(e))) === '{}')
    })
  })
})
