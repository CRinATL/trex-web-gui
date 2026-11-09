import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { useDashView } from '../dashView'
import { useHotkey } from '../keys'

// The open Columns panel: two check boxes that stay open while toggled. Escape (mounted only while open) and a press outside close it;
// Escape from inside the panel hands the focus back to the Columns button, so it does not fall to the page when the focused
// check box unmounts; Escape with the focus elsewhere (a dialog, the table) closes the panel and leaves the focus where it is.
function ColumnsPanel({ id, close, focusButton }: { id: string; close: () => void; focusButton: () => void }) {
  const { view, setView } = useDashView()
  const ref = useRef<HTMLFieldSetElement>(null)
  const escape = useCallback(() => {
    const inside = ref.current?.contains(document.activeElement) ?? false
    close()
    if (inside) focusButton()
  }, [close, focusButton])
  useHotkey('escape', escape)
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
    <fieldset id={id} className="columns-panel" ref={ref}>
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
  const button = useRef<HTMLButtonElement>(null)
  const focusButton = useCallback(() => button.current?.focus(), [])
  const id = useId()
  return (
    <span className="setups-menu">
      <button type="button" ref={button} aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)}>Columns ▾</button>
      {open && <ColumnsPanel id={id} close={close} focusButton={focusButton} />}
    </span>
  )
}

// The row above a Dashboard table: Columns ▾ (Streams only), Show history (Latency only) and the Full-width table toggle.
export function TableViewControls({ tab }: { tab: 'streams' | 'latency' }) {
  const { view, setView } = useDashView()
  return (
    <div className="table-view-controls">
      {tab === 'streams' && <ColumnsControl />}
      {tab === 'latency' && <button type="button" className="chip" aria-pressed={view.history} onClick={() => setView({ history: !view.history })}>Show history (Last … Last-9)</button>}
      <button type="button" className="chip" aria-pressed={view.wide} onClick={() => setView({ wide: !view.wide })}>Full-width table</button>
    </div>
  )
}
