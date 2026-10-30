import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import html from '../index.html?raw'
import { applyTheme, readTheme, resolveScheme, THEME_KEY, useTheme } from './theme'
import { setPrefersDark } from './testMatchMedia'

afterEach(() => { localStorage.clear(); delete document.documentElement.dataset.theme; vi.restoreAllMocks() })

describe('theme', () => {
  it('readTheme returns system when nothing is stored', () => { expect(readTheme()).toBe('system') })
  it('readTheme returns stored dark', () => { localStorage.setItem(THEME_KEY, 'dark'); expect(readTheme()).toBe('dark') })
  it('readTheme ignores an unknown value', () => { localStorage.setItem(THEME_KEY, 'neon'); expect(readTheme()).toBe('system') })
  it('falls back to system when localStorage throws', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied') })
    expect(readTheme()).toBe('system')
  })
  it('applyTheme sets and removes data-theme', () => {
    applyTheme('dark'); expect(document.documentElement.dataset.theme).toBe('dark')
    applyTheme('contrast'); expect(document.documentElement.dataset.theme).toBe('contrast')
    applyTheme('light'); expect(document.documentElement.dataset.theme).toBe('light')
    applyTheme('system'); expect(document.documentElement.dataset.theme).toBeUndefined()
  })
  it('resolveScheme: system follows prefersDark; light/dark/contrast fixed', () => {
    expect(resolveScheme('system', true)).toBe('dark')
    expect(resolveScheme('system', false)).toBe('light')
    for (const t of ['light', 'dark', 'contrast'] as const) {
      expect(resolveScheme(t, true)).toBe(t); expect(resolveScheme(t, false)).toBe(t)
    }
  })
  it('useTheme reacts to a matchMedia change under system', () => {
    const { result } = renderHook(() => useTheme())
    expect(result.current.theme).toBe('system'); expect(result.current.scheme).toBe('light')
    act(() => setPrefersDark(true))
    expect(result.current.scheme).toBe('dark')
    act(() => setPrefersDark(false))
    expect(result.current.scheme).toBe('light')
  })
  it('setTheme stores, applies and resolves', () => {
    const { result } = renderHook(() => useTheme())
    act(() => result.current.setTheme('dark'))
    expect(result.current.scheme).toBe('dark')
    expect(localStorage.getItem(THEME_KEY)).toBe('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
    act(() => result.current.setTheme('system'))
    expect(document.documentElement.dataset.theme).toBeUndefined()
  })
  it('works without matchMedia', async () => {
    const saved = window.matchMedia
    // @ts-expect-error jsdom has none; remove the mock for this one test
    delete window.matchMedia
    try {
      vi.resetModules()
      const fresh = await import('./theme')
      const { result } = renderHook(() => fresh.useTheme())
      expect(result.current.scheme).toBe('light')
    } finally { window.matchMedia = saved }
  })
  it('ignores the old theme key', () => {
    localStorage.setItem('trex-gui.theme', 'dark')
    expect(readTheme()).toBe('system')
    expect(() => applyTheme(readTheme())).not.toThrow()
    expect(document.documentElement.dataset.theme).toBeUndefined()
  })
  it('index.html pre-paint script uses THEME_KEY', () => {
    expect(THEME_KEY).toBe('trex-web-gui.theme')
    expect(html).toContain("localStorage.getItem('trex-web-gui.theme')")
  })
})
