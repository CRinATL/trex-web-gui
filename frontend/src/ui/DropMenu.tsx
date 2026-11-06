import { useCallback, useEffect, useRef, useState } from 'react'
import { useHotkey } from '../keys'

// One item of a DropMenu: picking it closes the menu first, then runs onPick.
export interface DropItem { id: string; label: string; onPick: () => void; disabled?: boolean; title?: string; danger?: boolean }

function MenuList({ name, items, close }: { name: string; items: DropItem[]; close: () => void }) {
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
  return (
    <div role="menu" aria-label={name} className="setups-menu-list" ref={ref}>
      {items.map((it) => (
        <button key={it.id} type="button" role="menuitem" className={it.danger ? 'danger' : undefined} disabled={it.disabled} title={it.title}
          onClick={() => { close(); it.onPick() }}>{it.label}</button>
      ))}
    </div>
  )
}

// "<name> ▾": a button that opens a short menu of items under it (Setups, the Control toolbar's More).
export function DropMenu({ name, items }: { name: string; items: DropItem[] }) {
  const [open, setOpen] = useState(false)
  const close = useCallback(() => setOpen(false), [])
  return (
    <span className="setups-menu">
      <button type="button" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>{name} ▾</button>
      {open && <MenuList name={name} items={items} close={close} />}
    </span>
  )
}
