import { useSyncExternalStore } from 'react'
import { pgidStore } from './pgidStats'
import { statsStore, type Snapshot } from './stats'

// Selectors return primitives: a fresh array or object per call makes useSyncExternalStore loop.
export function useStatsSelector<T extends number | string | boolean | null>(select: (s: Snapshot) => T): T {
  return useSyncExternalStore(statsStore.subscribe, () => select(statsStore.snapshot()))
}

// For the pgid store, which has no snapshot object: the selector reads the store directly and returns a primitive.
export function usePgidSelector<T extends number | string | boolean | null>(select: () => T): T {
  return useSyncExternalStore(pgidStore.subscribe, select)
}
