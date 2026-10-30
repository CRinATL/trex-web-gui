import { useEffect, useSyncExternalStore, type ReactNode } from 'react'
import { freeColor, SELECT_MAX } from '../pgidPalette'
import { pgidStore, reconcileSelection, type Tab } from '../pgidStats'

export interface Selection {
  selected: { id: number; color: string }[]   // in the order they were picked
  isSelected: (id: number) => boolean
  atCap: boolean
  toggle: (id: number) => void
}

// The chart selection of one tab, a view over pgidStore: the selection (and the chart history of its pg_ids) is kept while
// the page is away. The first AUTO_SELECT of `ids` are picked the first time any ids are seen; after that only the user
// changes it. A pg_id keeps its colour for as long as it stays picked, so unpicking one repaints no other. A pg_id that is
// no longer in `ids` drops out.
export function PgidSelector({ tab, ids, children }: { tab: Tab; ids: number[]; children: (s: Selection) => ReactNode }) {
  const stored = useSyncExternalStore(pgidStore.subscribe, () => pgidStore.selection(tab))
  // What the ids leave of the stored selection; rendered at once and written back below.
  const view = reconcileSelection(stored, ids)
  useEffect(() => { if (view !== stored) pgidStore.setSelection(tab, view) })
  const selected = view.picked
  const atCap = selected.length >= SELECT_MAX

  const toggle = (id: number) => {
    if (selected.some((p) => p.id === id)) { pgidStore.setSelection(tab, { ...view, picked: selected.filter((p) => p.id !== id) }); return }
    const color = freeColor(selected.map((p) => p.color))
    if (color) pgidStore.setSelection(tab, { ...view, picked: [...selected, { id, color }] })
  }
  return <>{children({ selected, isSelected: (id) => selected.some((p) => p.id === id), atCap, toggle })}</>
}
