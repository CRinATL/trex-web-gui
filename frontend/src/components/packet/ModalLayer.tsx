import type { KeyboardEvent, ReactNode } from 'react'

// A modal's keys are not the page's (nor the editor's), and Ctrl+S and Ctrl+O are not the browser's. Escape is left to
// travel on: each dialog closes itself from a window-level hotkey, which a stopped event would never reach.
function dialogKeys(e: KeyboardEvent<HTMLElement>) {
  if (e.key === 'Escape') return
  if ((e.ctrlKey || e.metaKey) && ['s', 'o'].includes(e.key.toLowerCase())) e.preventDefault()
  e.stopPropagation()
}

// The wrapper for a dialog rendered inside a page that has key handlers of its own (a context menu is no more the page's).
export function ModalLayer({ children }: { children: ReactNode }) {
  return <div onKeyDown={dialogKeys} onContextMenu={(e) => e.stopPropagation()}>{children}</div>
}
