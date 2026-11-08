import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { useDashView } from '../dashView'
import { useHotkey } from '../keys'

// The open Columns panel: two check boxes that stay open while toggled. Escape (mounted only while open) and a press outside close it.
function ColumnsPanel({ id, close }: { id: string; close: () => void }) {
  const { view, setView } = useDashView()
  useHotkey('escape', close)
  const ref = useRef<HTMLFieldSetElement>(null)
  // a press outside the whole control (its button included, so the button's own click can toggle) closes the panel
  useEffect(() => {
    const down = (e: MouseEvent) => {
      const root = ref.current?.parentElement
      if (root && !root.contains(e.target as Node)) close()
    }
    document.addEventListener('mousedown', down)
    return () => document.removeEventListener('mousedown', down)
  }, [close])
  return (
    <fieldset id={id} className="setups-menu-list" ref={ref}>
      <legend>Columns</legend>
      <label><input type="checkbox" checked={view.bitRates} onChange={(e) => setView({ bitRates: e.target.checked })} /> Bit rates (L1 and L2)</label>
      <label><input type="checkbox" checked={view.counters} onChange={(e) => setView({ counters: e.target.checked })} /> Packet and byte counters</label>
    </fieldset>
  )
}

// "Columns ▾": a disclosure button (not a menu) whose panel picks the optional Streams columns.
function ColumnsControl() {
  const [open, setOpen] = useState(false)
  const close = useCallback(() => setOpen(false), [])
  const id = useId()
  return (
    <span className="setups-menu">
      <button type="button" aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)}>Columns ▾</button>
      {open && <ColumnsPanel id={id} close={close} />}
    </span>
  )
}

// The row above a Dashboard table: Columns ▾ (Streams only) and the Full-width table toggle.
export function TableViewControls({ tab }: { tab: 'streams' | 'latency' }) {
  const { view, setView } = useDashView()
  return (
    <div className="table-view-controls">
      {tab === 'streams' && <ColumnsControl />}
      <button type="button" className="chip" aria-pressed={view.wide} onClick={() => setView({ wide: !view.wide })}>Full-width table</button>
    </div>
  )
}
