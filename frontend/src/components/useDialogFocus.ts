import { useEffect, useRef } from 'react'

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

// Moves focus into the dialog on mount (to the first element matching `selector`) and restores
// it to the previously focused element (the opener) on unmount.
export function useDialogFocus<T extends HTMLElement>(selector: string) {
  const ref = useRef<T>(null)
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    ref.current?.querySelector<HTMLElement>(selector)?.focus()
    const node = ref.current
    // keep Tab inside the dialog: wrap from the last focusable element to the first and back
    const trap = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.key !== 'Tab' || !node) return
      // a negative tabIndex (a roving-tabindex cell, `tabIndex={-1}`) is not a Tab stop, whatever matched the selector
      const items = Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => el.tabIndex >= 0)
      if (!items.length) return
      const first = items[0], last = items[items.length - 1]
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
    }
    node?.addEventListener('keydown', trap)
    return () => {
      node?.removeEventListener('keydown', trap)
      // the opener can be replaced while the dialog is open; fall back to the stable main landmark
      const target = opener?.isConnected ? opener : document.querySelector<HTMLElement>('main')
      target?.focus()
    }
  }, [selector])
  return ref
}
