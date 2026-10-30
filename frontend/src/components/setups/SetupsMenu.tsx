import { useCallback, useEffect, useRef, useState } from 'react'
import { useHotkey } from '../../keys'

function MenuList({ connected, onSave, onManage, close }: { connected: boolean; onSave: () => void; onManage: () => void; close: () => void }) {
  useHotkey('escape', close)       // mounted only while the menu is open, so a closed menu never swallows Escape
  const ref = useRef<HTMLDivElement>(null)
  // a press outside the whole menu (button included, so the button's own click can toggle) closes it
  useEffect(() => {
    const down = (e: MouseEvent) => {
      const root = ref.current?.parentElement
      if (root && !root.contains(e.target as Node)) close()
    }
    document.addEventListener('mousedown', down)
    return () => document.removeEventListener('mousedown', down)
  }, [close])
  const pick = (fn: () => void) => () => { close(); fn() }
  return (
    <div role="menu" aria-label="Setups" className="setups-menu-list" ref={ref}>
      <button type="button" role="menuitem" disabled={!connected} onClick={pick(onSave)}>Save current…</button>
      <button type="button" role="menuitem" onClick={pick(onManage)}>Manage setups…</button>
    </div>
  )
}

// "Setups ▾": a button that opens a two-item menu. The page owns the dialogs.
export function SetupsMenu({ connected, onSave, onManage }: { connected: boolean; onSave: () => void; onManage: () => void }) {
  const [open, setOpen] = useState(false)
  const close = useCallback(() => setOpen(false), [])
  return (
    <span className="setups-menu">
      <button type="button" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>Setups ▾</button>
      {open && <MenuList connected={connected} onSave={onSave} onManage={onManage} close={close} />}
    </span>
  )
}
