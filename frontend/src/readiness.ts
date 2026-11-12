import { useSyncExternalStore } from 'react'
import type { PortView } from './api'
import { defaultState, isRunning } from './portActions'

export type Step = 'acquire' | 'assign' | 'start' | 'running' | 'blocked'
export const TRAIL: readonly { step: Step; word: string }[] = [
  { step: 'acquire', word: 'Acquire' }, { step: 'assign', word: 'Assign a profile' }, { step: 'start', word: 'Start' },
]

// The next step for a port and the sentence that names it. Order matters: read-only, a port held by another session of
// the user, the unowned ports, then the owned ones (running, link down with streams, streams, and IDLE as the fallthrough).
export function readiness(port: PortView, readOnly: boolean): { step: Step; text: string } {
  if (readOnly) return { step: 'blocked', text: 'Read-only session: the port cannot be changed.' }
  if (port.mine_elsewhere) return { step: 'blocked', text: 'Held by another session of yours: use Re-acquire my ports.' }
  if (!port.owned) {
    if (port.owner === '') return { step: 'acquire', text: 'Acquire the port, then assign a profile and start.' }
    return { step: 'blocked', text: `Owned by ${port.owner}: use Force acquire to take it.` }
  }
  if (isRunning(port)) return { step: 'running', text: 'Traffic is running.' }
  const streams = defaultState(port) === 'STREAMS'
  if (streams && port.link_up === false) return { step: 'start', text: 'Link is down: Start on the Traffic tab may be refused by TRex.' }
  if (streams) return { step: 'start', text: 'Start on the Traffic tab.' }
  return { step: 'assign', text: 'Assign a profile on the Traffic tab, then start.' }
}

export const READINESS_HIDDEN_KEY = 'trex-web-gui.readiness-strip-hidden'

// Shown unless the key reads '1'; storage that throws leaves the strip shown.
function readShown(): boolean {
  try { return localStorage.getItem(READINESS_HIDDEN_KEY) !== '1' } catch { return true }
}

let snap: boolean = readShown()
const subs = new Set<() => void>()
const subscribe = (fn: () => void) => { subs.add(fn); return () => { subs.delete(fn) } }
const setShown = (v: boolean) => {
  try {
    if (v) localStorage.removeItem(READINESS_HIDDEN_KEY)
    else localStorage.setItem(READINESS_HIDDEN_KEY, '1')
  } catch { /* storage unavailable: the choice lasts until reload */ }
  if (v === snap) return
  snap = v
  subs.forEach((f) => f())
}

// Whether the readiness strip is shown, and a setter that saves the choice and notifies every subscriber.
export function useReadinessStrip(): { shown: boolean; setShown: (v: boolean) => void } {
  const shown = useSyncExternalStore(subscribe, () => snap)
  return { shown, setShown }
}

// Test-only: re-read the snapshot from storage and drop every listener, so one test's choice cannot leak into the next.
export function resetReadinessStore() {
  snap = readShown()
  subs.clear()
}
