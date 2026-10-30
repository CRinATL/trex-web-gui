import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { MenuAction } from '../portActions'
import { useDialogFocus } from './useDialogFocus'

const CONFIRM_MS = 5000
const errText = (x: unknown) => (x instanceof Error ? x.message : String(x))

// onError(null) reports a successful command, so the page can clear its alert line.
export function ContextMenu({ x, y, actions, onClose, onError }:
  { x: number; y: number; actions: MenuAction[]; onClose: () => void; onError: (message: string | null) => void }) {
  // useDialogFocus puts focus on the first enabled item and returns it to the opener (the tree row) on unmount
  const ref = useDialogFocus<HTMLDivElement>('[role="menuitem"]:not([aria-disabled="true"])')
  const [pos, setPos] = useState({ left: x, top: y })
  const [confirming, setConfirming] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const inFlight = useRef(false)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  useLayoutEffect(() => {
    const r = ref.current?.getBoundingClientRect()
    if (!r) return
    setPos({ left: Math.max(0, Math.min(x, window.innerWidth - r.width)), top: Math.max(0, Math.min(y, window.innerHeight - r.height)) })
  }, [x, y, ref])
  useEffect(() => () => clearTimeout(timer.current), [])
  // with nothing enabled to focus, the container takes focus so Escape still reaches onKeyDown
  useEffect(() => {
    const node = ref.current
    if (node && !node.querySelector('[role="menuitem"]:not([aria-disabled="true"])')) node.focus()
  }, [ref])
  useEffect(() => {
    const away = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) onClose() }
    document.addEventListener('mousedown', away)
    return () => document.removeEventListener('mousedown', away)
  }, [onClose, ref])

  async function activate(a: MenuAction) {
    if (!a.enabled || inFlight.current) return
    if (a.danger && confirming !== a.id) {
      setConfirming(a.id)
      clearTimeout(timer.current)
      timer.current = setTimeout(() => setConfirming(null), CONFIRM_MS)
      return
    }
    // A request only navigates (the page focuses a control in the Traffic tab). Close the menu in the same
    // batch, so its focus restore to the tree row runs before, not after, the page's focus.
    if (a.request) { onClose(); a.run().catch((x) => onError(errText(x))); return }
    inFlight.current = true; setBusy(true)
    try { await a.run(); onError(null) } catch (x) { onError(errText(x)) } finally { inFlight.current = false; onClose() }
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Tab') { e.preventDefault(); onClose(); return }
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); onClose(); return }
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
    e.preventDefault()
    const items = [...(ref.current?.querySelectorAll<HTMLElement>('[role="menuitem"]:not([aria-disabled="true"])') ?? [])]
    if (!items.length) return
    const i = items.indexOf(document.activeElement as HTMLElement)
    const next = e.key === 'ArrowDown' ? (i + 1) % items.length : (i <= 0 ? items.length - 1 : i - 1)
    items[next].focus()
  }

  return (
    <div role="menu" className="context-menu" ref={ref} tabIndex={-1} style={{ position: 'fixed', left: pos.left, top: pos.top }} onKeyDown={onKeyDown}>
      {actions.map((a) => (
        <button key={a.id} role="menuitem" type="button" aria-disabled={!a.enabled || busy} tabIndex={-1} className={a.danger ? 'danger' : undefined}
          onClick={() => void activate(a)}>
          {confirming === a.id ? (a.confirm ? `${a.confirm}. Click again to confirm` : 'Click again to confirm') : a.label}
        </button>
      ))}
    </div>
  )
}
