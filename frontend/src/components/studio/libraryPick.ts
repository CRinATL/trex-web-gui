import { inspect, type LibraryEntry } from '../../api'
import { defaultsFor } from '../../traffic'
import { trafficStore } from '../../trafficStore'

// Spec §6.1, Q10: a .py entry is assigned from its source, not from the frozen JSON.
export const PY_NOTE = 'Assigns the .py source with these tunables; editor changes to this entry are not sent.'

// The absolute path of the .py a library entry was imported from, or null for an entry backed by its JSON.
export const pySourceOf = (e: LibraryEntry): string | null => (e.source?.kind === 'py' && e.source.path ? e.source.path : null)

// A .py pick fills the form from the entry's source path and tunables. The Inspect reply's path equals `formPath` (the source
// path is absolute), so the kept values survive it. Nothing is inspected: that stays an explicit action.
export function fillFromPy(port: number, e: LibraryEntry, src: string): void {
  trafficStore.set(port, {
    path: src, formPath: src, inspected: null, specs: [], libraryPy: src, libraryArgs: e.source?.args ?? [],
    // an entry with args but no tunable form (made through the API) keeps its args as the extra arguments
    values: e.source?.tunables?.values ?? {}, extra: e.source?.tunables ? e.source.tunables.extra : (e.source?.args ?? []).join(' '),
  })
}

// A JSON-backed pick sets the path to the entry's file and inspects it at once, as picking a file in Browse does.
export async function fillFromJson(port: number, e: LibraryEntry): Promise<void> {
  const gen = trafficStore.generation()
  const i = await inspect({ path: e.path, ports: [port], tunables: [] })
  if (trafficStore.generation() !== gen) return
  trafficStore.set(port, { path: e.path, formPath: i.path, inspected: i, specs: i.tunables, values: defaultsFor(i.tunables), extra: '', libraryPy: null })
}
