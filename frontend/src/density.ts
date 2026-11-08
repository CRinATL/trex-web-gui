import { useSyncExternalStore } from 'react'

export type Density = 'comfortable' | 'compact'
export const DENSITY_KEY = 'trex-web-gui.density'   // frontend/index.html's pre-paint script reads this key too
export const DENSITIES: readonly (readonly [Density, string])[] = [['comfortable', 'Comfortable'], ['compact', 'Compact']]

// The stored density; anything but 'compact', and storage that throws, is comfortable.
export function readDensity(): Density {
  try {
    return localStorage.getItem(DENSITY_KEY) === 'compact' ? 'compact' : 'comfortable'
  } catch { return 'comfortable' }
}
const saveDensity = (d: Density) => { try { localStorage.setItem(DENSITY_KEY, d) } catch { /* storage unavailable: the choice lasts until reload */ } }

// compact sets data-density; comfortable removes it so the default control tokens apply.
export function applyDensity(d: Density) {
  if (d === 'compact') document.documentElement.dataset.density = 'compact'
  else delete document.documentElement.dataset.density
}

let snap: Density = readDensity()
const subs = new Set<() => void>()
const subscribe = (fn: () => void) => { subs.add(fn); return () => { subs.delete(fn) } }
const setDensity = (d: Density) => {
  saveDensity(d); applyDensity(d)
  if (d === snap) return
  snap = d
  subs.forEach((f) => f())
}

// The stored density and a setter that saves it, applies it and notifies every subscriber.
export function useDensity(): { density: Density; setDensity: (d: Density) => void } {
  const density = useSyncExternalStore(subscribe, () => snap)
  return { density, setDensity }
}

// Test-only: re-read the snapshot from storage and drop every listener, so one test's density cannot leak into the next.
export function resetDensityStore() {
  snap = readDensity()
  subs.clear()
}
