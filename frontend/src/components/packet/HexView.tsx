import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { rowAt, type Range } from './hexModel'

const MOVES: Record<string, (i: number, n: number) => number> = {
  ArrowRight: (i) => i + 1, ArrowLeft: (i) => i - 1, ArrowDown: (i) => i + 16, ArrowUp: (i) => i - 16,
  Home: () => 0, End: (_i, n) => n - 1,
}
const OVERSCAN = 5   // rows kept above and below the ones in view

export interface HexViewProps {
  bytes: Uint8Array
  selected: Range | null
  onSelectByte: (index: number) => void
  onEditByte?: (index: number, value: number) => void
  rowHeight?: number
  height?: number
}

// The frame, 16 bytes to a row: offset, hex and ASCII, the selected range marked. Only the rows in view and 5 above and
// below are in the document (a 9238-byte frame is 578 rows), plus the row of the byte that holds the tab stop.
// The bytes are one tab stop (roving tabindex): arrow keys, Home and End move between them, Enter or Space selects.
// With `onEditByte`, two hex digits typed on a byte replace it; Escape drops a half-typed byte.
export function HexView({ bytes, selected, onSelectByte, onEditByte, rowHeight = 20, height = 400 }: HexViewProps) {
  const [active, setActive] = useState(0)
  const [scrollTop, setScrollTop] = useState(0)
  const [typed, setTyped] = useState<{ index: number; digit: string } | null>(null)   // the first digit of a byte being typed
  const root = useRef<HTMLDivElement>(null)
  const focusNext = useRef<number | null>(null)
  const inside = (i: number) => selected !== null && i >= selected[0] && i < selected[1]
  const stop = Math.min(active, Math.max(0, bytes.length - 1))
  const rowCount = Math.ceil(bytes.length / 16)

  const first = Math.max(0, Math.floor(scrollTop / rowHeight) - OVERSCAN)
  const last = Math.min(rowCount - 1, Math.ceil((scrollTop + height) / rowHeight) + OVERSCAN)
  const stopRow = Math.floor(stop / 16)
  const rows: number[] = []
  if (rowCount > 0 && stopRow < first) rows.push(stopRow)
  for (let r = first; r <= last; r++) rows.push(r)
  if (rowCount > 0 && stopRow > last) rows.push(stopRow)

  // After a key moved the stop to a row that was not rendered, focus it once it is.
  useEffect(() => {
    const i = focusNext.current
    if (i === null) return
    const el = root.current?.querySelector<HTMLElement>(`[data-i="${i}"]`)
    if (el) { focusNext.current = null; el.focus() }
  })

  function reveal(row: number) {
    const el = root.current
    if (!el) return
    const top = row * rowHeight
    if (top < el.scrollTop) el.scrollTop = top
    else if (top + rowHeight > el.scrollTop + height) el.scrollTop = top + rowHeight - height
    setScrollTop(el.scrollTop)
  }

  function onKeyDown(e: KeyboardEvent) {
    if (bytes.length === 0) return
    if (onEditByte && !e.ctrlKey && !e.metaKey && !e.altKey && /^[0-9a-f]$/i.test(e.key)) {
      const at = e.target instanceof HTMLElement && e.target.dataset.i !== undefined ? Number(e.target.dataset.i) : null
      if (at === null) return
      e.preventDefault()
      if (typed !== null && typed.index === at) { setTyped(null); onEditByte(at, parseInt(typed.digit + e.key, 16)) }
      else setTyped({ index: at, digit: e.key.toLowerCase() })
      return
    }
    if (e.key === 'Escape' && typed !== null) { e.preventDefault(); e.stopPropagation(); setTyped(null); return }
    const move = MOVES[e.key]
    if (!move) return
    e.preventDefault()
    const next = Math.min(bytes.length - 1, Math.max(0, move(stop, bytes.length)))
    setTyped(null)
    setActive(next)
    reveal(Math.floor(next / 16))
    focusNext.current = next
    root.current?.querySelector<HTMLElement>(`[data-i="${next}"]`)?.focus()
  }

  // Rows are in normal flow between spacers (not positioned absolutely), so the view has the width of a row.
  const shown = Math.min(height, rowCount * rowHeight)
  const parts: ReactNode[] = []
  let next = 0
  for (const n of rows) {
    if (n > next) parts.push(<div key={`gap${next}`} aria-hidden="true" style={{ height: (n - next) * rowHeight }} />)
    const { offset, bytes: chunk, ascii } = rowAt(bytes, n)
    parts.push(
      <div className="hex-row" key={offset} style={{ height: rowHeight, lineHeight: `${rowHeight}px` }}>
        <span className="hex-offset">{offset.toString(16).padStart(4, '0')}</span>
        <span className="hex-bytes">
          {chunk.map((b, j) => {
            const i = offset + j
            const hex = b.toString(16).padStart(2, '0')
            const half = typed !== null && typed.index === i ? typed.digit : null
            return <button type="button" key={j} data-i={i} tabIndex={i === stop ? 0 : -1} className={inside(i) ? 'hex-byte marked' : 'hex-byte'}
              aria-label={`byte ${i}: 0x${hex}`} aria-pressed={inside(i)} onFocus={() => { setActive(i); if (typed !== null && typed.index !== i) setTyped(null) }}
              onClick={() => onSelectByte(i)}>{half !== null ? `${half}_` : hex}</button>
          })}
        </span>
        <span className="hex-ascii" aria-hidden="true">{Array.from(ascii, (c, j) => <span key={j} className={inside(offset + j) ? 'marked' : undefined}>{c}</span>)}</span>
      </div>)
    next = n + 1
  }
  if (next < rowCount) parts.push(<div key="end" aria-hidden="true" style={{ height: (rowCount - next) * rowHeight }} />)
  return (
    <div className="hex-view" role="group" aria-label="Packet bytes" ref={root} onKeyDown={onKeyDown}
      style={{ height: shown, overflowY: 'auto', scrollbarGutter: 'stable' }} onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}>
      {parts}
    </div>
  )
}
