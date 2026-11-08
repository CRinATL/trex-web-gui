import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { DENSITY_KEY, readDensity, resetDensityStore, useDensity } from './density'

describe('density', () => {
  beforeEach(() => { localStorage.clear(); resetDensityStore() })
  afterEach(() => { delete document.documentElement.dataset.density; vi.restoreAllMocks() })

  it('is comfortable with nothing stored', () => { expect(readDensity()).toBe('comfortable') })
  it('reads a stored compact', () => { localStorage.setItem(DENSITY_KEY, 'compact'); expect(readDensity()).toBe('compact') })
  it('reads an unknown stored value as comfortable', () => { localStorage.setItem(DENSITY_KEY, 'tiny'); expect(readDensity()).toBe('comfortable') })
  it('reads comfortable when getItem throws', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied') })
    expect(readDensity()).toBe('comfortable')
  })
  it('setDensity saves the key and sets or removes data-density', () => {
    const { result } = renderHook(() => useDensity())
    expect(result.current.density).toBe('comfortable')
    act(() => result.current.setDensity('compact'))
    expect(result.current.density).toBe('compact')
    expect(localStorage.getItem(DENSITY_KEY)).toBe('compact')
    expect(document.documentElement.dataset.density).toBe('compact')
    act(() => result.current.setDensity('comfortable'))
    expect(result.current.density).toBe('comfortable')
    expect(document.documentElement.dataset.density).toBeUndefined()
  })
  it('setDensity still works when setItem throws', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('full') })
    const { result } = renderHook(() => useDensity())
    act(() => result.current.setDensity('compact'))
    expect(result.current.density).toBe('compact')
    expect(document.documentElement.dataset.density).toBe('compact')
  })
})
