import { pgidStore } from './pgidStats'
import { statsStore } from './stats'

// The Reset displayed counters buttons' tooltip: the label now says its scope (A2.2 names the legacy one), and the hint adds that nothing is sent to TRex.
export const CLEAR_STATS_HINT = "Resets the counters shown in this browser; nothing is sent to TRex"

// The Reset displayed counters button (A8.10): browser-side only, sends nothing to the server.
export function clearAll(): void {
  statsStore.clear()
  pgidStore.clear()
}
