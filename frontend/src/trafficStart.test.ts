import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from './api'
import { trafficStore } from './trafficStore'
import { dropPending, FormError, startFromForm } from './trafficStart'

afterEach(() => { vi.restoreAllMocks(); trafficStore.reset() })

describe('startFromForm', () => {
  it('an untouched form starts at x1 with no duration limit', async () => {
    const st = vi.spyOn(api, 'start').mockResolvedValue({ ports: [] })
    await startFromForm(0)
    expect(st).toHaveBeenCalledWith({ ports: [0], mult: '1', duration: -1 })
  })
  it('a bad multiplier throws FormError and calls nothing', async () => {
    const st = vi.spyOn(api, 'start')
    trafficStore.set(0, { multValue: '0' })
    await expect(startFromForm(0)).rejects.toBeInstanceOf(FormError)
    await expect(startFromForm(0)).rejects.toThrow('Enter a multiplier greater than 0')
    trafficStore.set(0, { multValue: '1', duration: '-3' })
    await expect(startFromForm(0)).rejects.toThrow('Duration must be a positive number of seconds, or empty for no limit')
    expect(st).not.toHaveBeenCalled()
  })
  it('force is sent only when ticked', async () => {
    const st = vi.spyOn(api, 'start').mockResolvedValue({ ports: [] })
    trafficStore.set(0, { multValue: '10', unit: 'kpps', duration: '5', force: true })
    await startFromForm(0)
    expect(st).toHaveBeenLastCalledWith({ ports: [0], mult: '10kpps', duration: 5, force: true })
    trafficStore.set(0, { force: false })
    await startFromForm(0)
    expect(st).toHaveBeenLastCalledWith({ ports: [0], mult: '10kpps', duration: 5 })
  })
  it('success records applied and clears the countdown; a reset in between writes nothing', async () => {
    let release: () => void = () => undefined
    vi.spyOn(api, 'start').mockImplementation(() => new Promise((r) => { release = () => r({ ports: [] }) }))
    trafficStore.set(0, { multValue: '3', unit: 'mpps', pending: { mult: '4mpps', left: 5 } })
    const p = startFromForm(0)
    expect(trafficStore.get(0).pending).toBeNull()
    release(); await p
    expect(trafficStore.get(0).applied).toEqual({ value: '3', unit: 'mpps' })
    trafficStore.set(0, { multValue: '9' })
    const q = startFromForm(0)
    trafficStore.reset()
    release(); await q
    expect(trafficStore.all()).toEqual({})
  })
})

describe('dropPending', () => {
  it('clears the countdown of each port', () => {
    trafficStore.set(0, { pending: { mult: '2', left: 4 } })
    trafficStore.set(1, { pending: { mult: '3', left: 4 } })
    trafficStore.set(2, { pending: { mult: '5', left: 4 } })
    dropPending([0, 1])
    expect(trafficStore.get(0).pending).toBeNull()
    expect(trafficStore.get(1).pending).toBeNull()
    expect(trafficStore.get(2).pending).not.toBeNull()
  })
})
