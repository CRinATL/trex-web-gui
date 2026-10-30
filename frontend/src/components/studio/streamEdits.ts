import type { DiffReply, SidecarEntry, StreamJson, StreamRow, StreamsDiff } from '../../api'

// The diffs the stream table sends (R5: one diff per action, saved at once). Every builder is pure; none sends a whole
// stream as a patch, so a write queued behind another cannot undo the other's fields (opus I3).

const range = (from: number, to: number) => Array.from({ length: Math.max(0, to - from) }, (_, i) => from + i)
const copyOf = (s: StreamJson): StreamJson => JSON.parse(JSON.stringify(s)) as StreamJson

// Each selected stream moves one place in `dir`, so a block moves as one; null when the move would pass an edge or
// nothing is selected. The selected streams' new indices are their old ones plus `dir`.
export function reorderDiff(count: number, selected: number[], dir: -1 | 1, rev: string): StreamsDiff | null {
  const sel = new Set(selected.filter((i) => i >= 0 && i < count))
  if (sel.size === 0 || sel.has(dir === -1 ? 0 : count - 1)) return null
  const order = range(0, count)
  const swap = (i: number, j: number) => { [order[i], order[j]] = [order[j], order[i]] }
  if (dir === -1) { for (let i = 1; i < count; i++) if (sel.has(order[i]) && !sel.has(order[i - 1])) swap(i, i - 1) }
  else for (let i = count - 2; i >= 0; i--) if (sel.has(order[i]) && !sel.has(order[i + 1])) swap(i, i + 1)
  return { rev, order }
}

export function deleteDiff(selected: number[], rev: string): StreamsDiff {
  return { rev, removed: [...new Set(selected)].sort((a, b) => a - b) }
}

// `streams` (copied) go after old index `after`, or are appended when it is null. The backend renames a name that is
// taken (R7) and clears a `next` that names nothing here (ruling Task 2 M3, revised again).
// `entries` (parallel to `streams`) are the sidecar entries the copies carry, sent as `added_sidecar` when any is not empty.
export function pasteDiff(count: number, after: number | null, streams: StreamJson[], rev: string, entries?: SidecarEntry[]): StreamsDiff {
  const added = streams.map(copyOf)
  const side = entries?.some((e) => Object.keys(e).length > 0) ? { added_sidecar: entries.map((e) => JSON.parse(JSON.stringify(e)) as SidecarEntry) } : {}
  if (after === null) return { rev, added, ...side }
  return { rev, added, ...side, order: [...range(0, after + 1), ...added.map((_, k) => `+${k}`), ...range(after + 1, count)] }
}

// The selected streams found in `rows` (a page), in index order, pasted after the last selected one; null when none is.
// `entries` holds the sidecar entries of those streams by index; each copy carries its original's.
export function duplicateDiff(rows: StreamRow[], selected: number[], count: number, rev: string, entries: Record<number, SidecarEntry> = {}): StreamsDiff | null {
  const picked = rows.filter((r) => selected.includes(r.index)).sort((a, b) => a.index - b.index)
  if (picked.length === 0) return null
  return pasteDiff(count, Math.max(...picked.map((r) => r.index)), picked.map((r) => r.stream), rev, picked.map((r) => entries[r.index] ?? {}))
}

export function toggleDiff(index: number, enabled: boolean, rev: string): StreamsDiff {
  return { rev, changed: { [String(index)]: { enabled } } }
}

// null removes the name (a `next` that named it is cleared and reported by the backend).
export function renameDiff(index: number, newName: string | number | null, rev: string): StreamsDiff {
  return { rev, changed: { [String(index)]: { name: newName } } }
}

// The status line after a paste or duplicate: the names the backend gave instead of taken ones, and the streams whose
// `next` it cleared because the stream it named is not in this profile (ruling Task 2 M3, revised again). The editor's
// pcap append shows the same line.
export function addedText(verb: string, r: DiffReply): string {
  const parts: string[] = []
  const renamed = Object.values(r.renamed)
  if (renamed.length) parts.push(`${verb} as ${renamed.join(', ')} (names were taken).`)
  if (r.cleared.length) parts.push(`${r.cleared.join(', ')} now stop: the stream they went to is not in this profile.`)
  return parts.join(' ')
}
