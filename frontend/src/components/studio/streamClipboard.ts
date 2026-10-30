import type { SidecarEntry, StreamJson } from '../../api'

// The in-app stream clipboard (spec §6.2): module-level, so it survives navigating between pages and profiles.
// Both directions copy, so neither the source page nor a paste can change what is held. Each stream carries its
// sidecar entry (`{}` when it has none), so a paste keeps a stream's editor document.
let held: StreamJson[] = []
let heldEntries: SidecarEntry[] = []
const copyAll = <T,>(items: T[]) => JSON.parse(JSON.stringify(items)) as T[]

export function copyStreams(streams: StreamJson[], entries?: SidecarEntry[]): void {
  held = copyAll(streams)
  heldEntries = copyAll(streams.map((_, i) => entries?.[i] ?? {}))
}
export function pastedStreams(): StreamJson[] { return copyAll(held) }
export function pastedEntries(): SidecarEntry[] { return copyAll(heldEntries) }
