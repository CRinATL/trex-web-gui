import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

const GAP = 6      // px between the button and the note
const MARGIN = 8   // px kept clear of the viewport edge

// A small ⓘ button that reveals a note. The note floats: it is portalled to document.body (so a scrolling dialog cannot clip it),
// fixed under the button and clamped to the viewport, and it stays in the DOM while closed (hidden).
// Escape closes it and is swallowed, so an enclosing dialog's window-level Escape handler does not also fire.
// An outside pointerdown, a second click on the button, or the button being hidden closes it too.
export function Info({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState<CSSProperties>({ top: 0, left: 0 })
  const btn = useRef<HTMLButtonElement>(null)
  const note = useRef<HTMLDivElement>(null)
  const noteId = useId()

  // Places the note under the button, then pulls it inside the viewport.
  useLayoutEffect(() => {
    if (!open || !btn.current || !note.current) return
    const b = btn.current.getBoundingClientRect()
    const n = note.current.getBoundingClientRect()
    const vw = document.documentElement.clientWidth, vh = window.innerHeight
    let left = Math.min(b.left, vw - n.width - MARGIN)
    left = Math.max(MARGIN, left)
    let top = b.bottom + GAP
    if (top + n.height > vh - MARGIN && b.top - GAP - n.height >= MARGIN) top = b.top - GAP - n.height   // no room below: go above
    setPos({ top, left })
  }, [open])

  useEffect(() => {
    if (!open) return
    function away(e: PointerEvent) {
      const t = e.target as Node
      if (btn.current?.contains(t) || note.current?.contains(t)) return
      setOpen(false)
    }
    // The note is fixed where it opened, so anything that moves the button closes it: a resize, or a scroll of the page or of
    // an ancestor of the button such as a modal (scroll does not bubble, hence the capture phase). A scroll elsewhere (a live
    // table or log that auto-scrolls) leaves the button where it is and the note open.
    function close() { setOpen(false) }
    function scrolled(e: Event) {
      const t = e.target
      if (t === document || (t instanceof Node && btn.current && t.contains(btn.current))) setOpen(false)
    }
    // A button hidden with no pointer event (a hotkey switches the page or an arrow key the tab; both stay mounted, hidden)
    // would leave the portalled note floating over what replaced it: close it once the button is no longer visible.
    // Where IntersectionObserver is missing (jsdom) this is skipped; the other ways to close still work.
    let io: IntersectionObserver | null = null
    if (btn.current && typeof IntersectionObserver !== 'undefined') {
      io = new IntersectionObserver((entries) => { if (entries.some((e) => !e.isIntersecting)) setOpen(false) })
      io.observe(btn.current)
    }
    document.addEventListener('pointerdown', away)
    window.addEventListener('resize', close)
    window.addEventListener('scroll', scrolled, true)
    return () => {
      io?.disconnect()
      document.removeEventListener('pointerdown', away)
      window.removeEventListener('resize', close)
      window.removeEventListener('scroll', scrolled, true)
    }
  }, [open])

  // Closes the note on Escape and stops the event reaching window listeners; other keys pass through.
  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key !== 'Escape' || !open) return
    e.stopPropagation(); e.nativeEvent.stopImmediatePropagation()
    setOpen(false)
  }

  return (
    <span className="info-wrap" onKeyDown={onKeyDown}>
      <button ref={btn} type="button" className="info" aria-label={`About ${label}`} aria-expanded={open} aria-controls={noteId} onClick={() => setOpen((o) => !o)}>ⓘ</button>
      {createPortal(<div ref={note} id={noteId} className="info-text" role="note" hidden={!open} style={pos}>{children}</div>, document.body)}
    </span>
  )
}
