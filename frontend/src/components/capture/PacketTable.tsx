import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import type { DecodedRow } from '../../api'

export const PAGE_SIZE = 100   // as StreamsTable.tsx
const NUMERIC = new Set(['No.', 'Time', 'Port', 'Len'])
const COLUMNS = ['No.', 'Time', 'Port', 'Mode', 'Src', 'Dst', 'Type', 'Len', 'Info']

// The monitor's rows, 100 to a page. The newest page is followed unless the user picked another one (picking the last page
// follows again). One row is selected by its packet number; Enter or a double click opens it.
// `empty` is the sentence an empty table shows under its header, so the header never stands alone.
export function PacketTable({ rows, onOpen, empty }: { rows: DecodedRow[]; onOpen: (row: DecodedRow) => void; empty: string }) {
  const [chosen, setChosen] = useState<number | null>(null)
  const [selected, setSelected] = useState<number | null>(null)
  const tableRef = useRef<HTMLTableElement>(null)
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE))
  const cur = chosen === null ? pages - 1 : Math.min(chosen, pages - 1)
  const first = cur * PAGE_SIZE
  const shown = rows.slice(first, first + PAGE_SIZE)

  const activeId = shown.some((r) => r.index === selected) ? `pkt-${selected}` : undefined

  // Select the row at `pos` in `rows`, moving to its page when it is on another one (the last page follows again).
  function select(pos: number) {
    setSelected(rows[pos].index)
    const page = Math.floor(pos / PAGE_SIZE)
    if (page !== cur) setChosen(page === pages - 1 ? null : page)
  }

  function onKeyDown(e: KeyboardEvent) {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      if (rows.length === 0) return
      const down = e.key === 'ArrowDown'
      const at = shown.findIndex((r) => r.index === selected)
      if (at < 0) { select(first + (down ? 0 : shown.length - 1)); return }
      select(Math.min(rows.length - 1, Math.max(0, first + at + (down ? 1 : -1))))
    } else if (e.key === 'Enter') {
      // Only a row on the visible page: Enter must not open something the user cannot see.
      const row = shown.find((r) => r.index === selected)
      if (row) { e.preventDefault(); onOpen(row) }
    }
  }

  // Keep the selected row visible (jsdom has no scrollIntoView).
  useEffect(() => {
    const el = tableRef.current?.querySelector<HTMLElement>('tr[aria-selected="true"]')
    if (el && typeof el.scrollIntoView === 'function') el.scrollIntoView({ block: 'nearest' })
  }, [selected, cur])

  return (
    <div className="packet-table">
      {rows.length > PAGE_SIZE && (
        <div className="pager">
          <label>Page <select aria-label="Page" value={cur} onChange={(e) => { const n = Number(e.target.value); setChosen(n === pages - 1 ? null : n) }}>
            {Array.from({ length: pages }, (_, i) => <option key={i} value={i}>{i + 1}</option>)}
          </select></label>
          <span> of {pages} ({first + 1}–{first + shown.length} of {rows.length})</span>
        </div>
      )}
      <table className="data-table" role="grid" ref={tableRef} aria-label="Captured packets" aria-rowcount={rows.length === 0 ? 2 : rows.length + 1} aria-activedescendant={activeId} tabIndex={0} onKeyDown={onKeyDown}>
        <thead><tr>{COLUMNS.map((c) => <th key={c} scope="col" role="columnheader" className={NUMERIC.has(c) ? 'num' : undefined}>{c}</th>)}</tr></thead>
        <tbody>
          {rows.length === 0 && <tr><td role="gridcell" colSpan={COLUMNS.length} className="muted data-table-empty">{empty}</td></tr>}
          {shown.map((r, i) => (
            <tr key={r.index} id={`pkt-${r.index}`} aria-rowindex={first + i + 2} aria-selected={r.index === selected} className={r.index === selected ? 'selected' : undefined}
              onClick={() => setSelected(r.index)} onDoubleClick={() => { setSelected(r.index); onOpen(r) }}>
              <td role="gridcell" className="num">{r.index}</td><td role="gridcell" className="num">{(r.ts_rel_us / 1e6).toFixed(6)}</td>
              <td role="gridcell" className="num">{r.port}</td><td role="gridcell">{r.origin}</td>
              <td role="gridcell" className="mono">{r.src}</td><td role="gridcell" className="mono">{r.dst}</td><td role="gridcell">{r.proto}</td>
              <td role="gridcell" className="num">{r.wirelen}</td><td role="gridcell" className="wrap">{r.info}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
