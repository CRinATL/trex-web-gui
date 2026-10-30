import { useSyncExternalStore } from 'react'

export type Theme = 'system' | 'light' | 'dark' | 'contrast'
export type Scheme = 'light' | 'dark' | 'contrast'
export const THEME_KEY = 'trex-web-gui.theme'   // frontend/index.html's pre-paint script reads this key too
export const THEMES: readonly (readonly [Theme, string])[] = [['system', 'System'], ['light', 'Light'], ['dark', 'Dark'], ['contrast', 'High contrast']]

export function readTheme(): Theme {
  try {
    const v = localStorage.getItem(THEME_KEY)
    return v === 'light' || v === 'dark' || v === 'contrast' ? v : 'system'
  } catch { return 'system' }
}
export const saveTheme = (t: Theme) => { try { localStorage.setItem(THEME_KEY, t) } catch { /* storage unavailable: the choice lasts until reload */ } }

// light, dark and contrast set data-theme; system removes it so the prefers-color-scheme block applies.
export function applyTheme(t: Theme) {
  if (t === 'system') delete document.documentElement.dataset.theme
  else document.documentElement.dataset.theme = t
}

export const resolveScheme = (t: Theme, prefersDark: boolean): Scheme => (t === 'system' ? (prefersDark ? 'dark' : 'light') : t)

const QUERY = '(prefers-color-scheme: dark)'
const hasMedia = () => typeof window !== 'undefined' && typeof window.matchMedia === 'function'
const prefersDarkNow = () => hasMedia() && window.matchMedia(QUERY).matches

type Snap = { theme: Theme; scheme: Scheme }
let snap: Snap = { theme: readTheme(), scheme: resolveScheme(readTheme(), prefersDarkNow()) }
const subs = new Set<() => void>()
let mql: MediaQueryList | null = null

function refresh(theme: Theme) {
  const scheme = resolveScheme(theme, prefersDarkNow())
  if (theme === snap.theme && scheme === snap.scheme) return
  snap = { theme, scheme }
  subs.forEach((f) => f())
}
const onMedia = () => refresh(snap.theme)

function subscribe(fn: () => void) {
  subs.add(fn)
  if (subs.size === 1 && hasMedia()) { mql = window.matchMedia(QUERY); mql.addEventListener('change', onMedia); refresh(snap.theme) }
  return () => {
    subs.delete(fn)
    if (subs.size === 0 && mql) { mql.removeEventListener('change', onMedia); mql = null }
  }
}

const setTheme = (t: Theme) => { saveTheme(t); applyTheme(t); refresh(t) }

// The stored theme and its resolved scheme; the scheme follows prefers-color-scheme live while the theme is system.
export function useTheme(): { theme: Theme; scheme: Scheme; setTheme: (t: Theme) => void } {
  const s = useSyncExternalStore(subscribe, () => snap)
  return { theme: s.theme, scheme: s.scheme, setTheme }
}

// Test-only: re-read the snapshot from storage and matchMedia and drop every listener, so one test's theme cannot leak into the next.
export function resetThemeStore() {
  const theme = readTheme()
  snap = { theme, scheme: resolveScheme(theme, prefersDarkNow()) }
  subs.clear()
  if (mql) { mql.removeEventListener('change', onMedia); mql = null }
}
