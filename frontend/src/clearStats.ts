import { pgidStore } from './pgidStats'
import { statsStore } from './stats'

// The Clear stats buttons' tooltip: the label is the legacy one (A2.2), so the hint says what it does.
export const CLEAR_STATS_HINT = "Resets the counters shown in this browser; nothing is sent to TRex"

// The Clear stats button (A8.10): browser-side only, sends nothing to the server.
export function clearAll(): void {
  statsStore.clear()
  pgidStore.clear()
}
