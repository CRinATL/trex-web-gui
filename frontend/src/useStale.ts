import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { useAppState } from './state'
import { pgidStore } from './pgidStats'
import { statsStore } from './stats'
import { utilStore } from './utilStats'

// Where one read's arrival time comes from. interval is the read's own period in seconds, if it is known.
export interface StaleSource {
  receivedAt: () => number | null
  interval: () => number | undefined
  subscribe: (fn: () => void) => () => void
}

export const statsSource: StaleSource = { receivedAt: statsStore.receivedAt, interval: () => statsStore.latest()?.interval_s, subscribe: statsStore.subscribe }
export const pgidSource: StaleSource = { receivedAt: pgidStore.receivedAt, interval: () => pgidStore.latest()?.interval_s, subscribe: pgidStore.subscribe }
// A util message carries no interval of its own; the backend publishes it on the stats tick, so stats' interval applies.
export const utilSource: StaleSource = { receivedAt: utilStore.receivedAt, interval: () => statsStore.latest()?.interval_s, subscribe: utilStore.subscribe }

// A read that has not delivered one message since connect is judged against this when nothing tells its period: the
// stats tick is 1 s by default.
const FALLBACK_INTERVAL_S = 1

// The stats, pgid and util reads run on the read-only reader client, so a ping, scan or Apply holding the control executor no
// longer holds them back: measured 2026-10-18, the largest stats gap during a failing-resolve Apply was 1.013-1.016 s
// (stats_s 1.0), against 3.05-3.16 s before. The threshold is therefore the plain
// 3 x interval_s whether or not a port is busy. pgid and util share the reader's stats tick (trex_session.py _stats_loop),
// which is what lets the same plain threshold apply to them.

// True when no message of this read has arrived for 3 x interval_s. Arrival time is on the browser's monotonic clock, not ts:
// a skewed or stepping wall clock cannot fake or hide staleness. Re-checked every second, so it lags by up to 1 s.
// A read that never delivers anything (it fails from connect onward) is stale once the session has been connected for
// 3 x interval_s; the interval is the source's own, else the stats one, else FALLBACK_INTERVAL_S. That wait counts from when
// this hook saw the session connected, so a hook mounted late waits a little longer; it never reports early.
export function useStale(source: StaleSource = statsSource): boolean {
  // The arrival time of the newest message; a later message changes it, which hides the marker at once.
  const at = useSyncExternalStore(source.subscribe, source.receivedAt)
  const sessionState = useAppState().session.state
  // performance.now() when the session was first seen connected; null while it is connecting or disconnected.
  const connectedAt = useRef<number | null>(null)
  useEffect(() => {
    if (sessionState === 'connected') connectedAt.current ??= performance.now()
    else if (sessionState === 'connecting' || sessionState === 'disconnected') connectedAt.current = null
  }, [sessionState])
  // The arrival time the last tick judged stale; null means stale with no message yet, undefined means not stale.
  const [staleAt, setStaleAt] = useState<number | null | undefined>(undefined)
  useEffect(() => {
    const id = setInterval(() => {
      const last = source.receivedAt()
      const interval = source.interval() ?? statsStore.latest()?.interval_s ?? FALLBACK_INTERVAL_S
      const since = last ?? connectedAt.current
      const stale = since !== null && performance.now() - since > 3 * interval * 1000
      setStaleAt(stale ? last : undefined)
    }, 1000)
    return () => clearInterval(id)
  }, [source])
  return staleAt !== undefined && staleAt === at
}
