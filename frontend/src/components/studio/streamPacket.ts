import { docFromBinary, SIDECAR_ENTRY_MAX, streamSidecar, type PacketDoc, type SidecarEntry, type StreamJson, type StreamPatch, type StreamsDiff, type toStream } from '../../api'

// A stream's packet as the editor's document, and the diff that saves the edited document back (D7, D8).
export const NOTE_TOO_LARGE = 'the editor document is too large to keep; the stream reopens from its bytes'
export const NOTE_SIDECAR_LOST = 'the stream\'s saved editor document could not be read; the packet was opened from its bytes'

type Built = Awaited<ReturnType<typeof toStream>>
export interface MacEdits { src: boolean; dst: boolean }

const sizeOf = (e: SidecarEntry) => new TextEncoder().encode(JSON.stringify(e)).length   // the backend counts the same compact JSON

// Deep equality of two JSON values, whatever order their keys came in.
export function same(a: unknown, b: unknown): boolean {
  if (a === b) return true
  if (typeof a !== 'object' || typeof b !== 'object' || a === null || b === null) return false
  if (Array.isArray(a) !== Array.isArray(b)) return false
  const ka = Object.keys(a), kb = Object.keys(b)
  if (ka.length !== kb.length) return false
  return ka.every((k) => Object.prototype.hasOwnProperty.call(b, k) && same((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k]))
}

const isDoc = (d: unknown): d is PacketDoc => typeof d === 'object' && d !== null && Array.isArray((d as PacketDoc).packet)

// The document the editor opens for `row`. A row with an editor document in its sidecar entry uses it while the entry's
// bytes and VM are still the stream's (D7); a stream changed elsewhere since, or one without an entry, is derived from its
// bytes and VM. An entry that cannot be read also falls back to the bytes, and says so.
export async function docForStream(slug: string, row: { index: number; has_editor: boolean; stream: Pick<StreamJson, 'packet' | 'vm'> }): Promise<{ doc: PacketDoc; fromSidecar: boolean; warnings: string[] }> {
  const { binary } = row.stream.packet
  const lost: string[] = []
  if (row.has_editor) {
    try {
      const { entry } = await streamSidecar(slug, row.index)
      const ed = entry.editor as { doc?: unknown; binary?: unknown; vm?: unknown } | undefined
      if (ed && ed.binary === binary && same(ed.vm, row.stream.vm) && isDoc(ed.doc)) return { doc: ed.doc, fromSidecar: true, warnings: [] }
    } catch { lost.push(NOTE_SIDECAR_LOST) }
  }
  const r = await docFromBinary(binary, row.stream.vm)
  return { doc: r.doc, fromSidecar: false, warnings: [...lost, ...r.warnings] }
}

// Which MAC addresses of the first Ether layer differ between two documents.
export function macEdits(before: PacketDoc, after: PacketDoc): MacEdits {
  const of = (d: PacketDoc, id: string) => {
    const l = d.packet[0]
    return l?.id === 'Ether' ? JSON.stringify(l.fields.find((f) => f.id === id)?.value ?? null) : null
  }
  return { src: of(before, 'src') !== of(after, 'src'), dst: of(before, 'dst') !== of(after, 'dst') }
}

// The flags a Save writes, or undefined to leave them: a preset's flags win; otherwise an Ether.src edit sets bit 0 (the
// source MAC comes from the packet) and an Ether.dst edit sets the destination mode (bits 1-2) to 1 (B2.8).
function flagsFor(current: number, mac: MacEdits, preset: number | null): number | undefined {
  if (preset !== null) return preset
  if (!mac.src && !mac.dst) return undefined
  let f = current
  if (mac.src) f |= 1
  if (mac.dst) f = (f & ~6) | 2
  return f
}

// The diff that saves the edited packet of stream `index`: the new bytes with the stream's own `meta` (opus M7), the VM,
// possibly the MAC flags, and the sidecar entry that reopens the document, or `{}` when the entry is over
// SIDECAR_ENTRY_MAX (see `tooLarge`).
export function saveDiff(index: number, stream: StreamJson, doc: PacketDoc, built: Built, macEdited: MacEdits, presetFlags: number | null, rev: string): StreamsDiff {
  const flags = flagsFor(stream.flags, macEdited, presetFlags)
  const patch: StreamPatch = {
    packet: { binary: built.packet.binary, ...(stream.packet.meta === undefined ? {} : { meta: stream.packet.meta }) },
    vm: built.vm,
    ...(flags === undefined ? {} : { flags }),
  }
  const full: SidecarEntry = { editor: { doc, binary: built.packet.binary, vm: built.vm } }
  return { rev, changed: { [String(index)]: patch }, sidecar: { [String(index)]: sizeOf(full) > SIDECAR_ENTRY_MAX ? {} : full } }
}

// The note for a diff `saveDiff` made, when it left the document out.
export const tooLarge = (diff: StreamsDiff, index: number): string | null =>
  Object.keys(diff.sidecar?.[String(index)] ?? {}).length === 0 ? NOTE_TOO_LARGE : null
