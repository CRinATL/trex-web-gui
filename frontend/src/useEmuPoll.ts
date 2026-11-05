import { useEffect, useState } from 'react'
import { ApiError } from './api'
import { useEmu } from './emuStore'

// True while the browser shows this page (document.visibilityState), following visibilitychange.
function usePageVisible(): boolean {
  const [v, setV] = useState(document.visibilityState === 'visible')
  useEffect(() => {
    const on = () => setV(document.visibilityState === 'visible')
    document.addEventListener('visibilitychange', on)
    on()
    return () => document.removeEventListener('visibilitychange', on)
  }, [])
  return v
}

export const NS_POLL_MS = 5000
export const CLIENTS_POLL_MS = 5000
export const COUNTERS_POLL_MS = 1000

export interface EmuPoll<T> { data: T | null; error: ApiError | Error | null }

// Reads once as it becomes active and again each time `deps` change, then every `intervalMs` after the previous read settles (so
// none overlaps). It is active only while the page is visible and the EMU state is `connected`; otherwise it reads nothing. The last
// good data stays after a failed read within one `deps` value (the error is reported beside it); a change of `deps`, or the state leaving
// `connected`, drops data and error, so one scope's rows never show under another's label. A reply that arrives after the effect was torn down
// (new deps, hidden page, unmount) is dropped. The namespaces and clients tables use 5000, the counters panel 1000.
export function useEmuPoll<T>(read: () => Promise<T>, intervalMs: number, deps: readonly unknown[]): EmuPoll<T> {
  const visible = usePageVisible()
  const connected = useEmu((m) => m?.state === 'connected')
  const active = visible && connected
  const key = JSON.stringify(deps)
  const [stored, setStored] = useState<EmuPoll<T> & { key: string }>({ key, data: null, error: null })
  // A disconnected (or reconnected) session starts empty; a hidden page keeps its data.
  useEffect(() => () => setStored({ key: '', data: null, error: null }), [connected])
  const state: EmuPoll<T> = stored.key === key ? stored : { data: null, error: null }
  useEffect(() => {
    if (!active) return
    let live = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const once = () => {
      read().then(
        (d) => { if (live) setStored({ key, data: d, error: null }) },
        (x: unknown) => { if (live) setStored((s) => ({ key, data: s.key === key ? s.data : null, error: x instanceof Error ? x : new Error(String(x)) })) },
      ).finally(() => { if (live) timer = setTimeout(once, intervalMs) })
    }
    once()
    return () => { live = false; if (timer !== undefined) clearTimeout(timer) }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `read` is a fresh closure each render; `deps` names what it depends on
  }, [active, intervalMs, key])
  return state
}
