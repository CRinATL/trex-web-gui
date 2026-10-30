import { useEffect } from 'react'

export function matches(e: KeyboardEvent, combo: string): boolean {
  const parts = combo.toLowerCase().split('+')
  const key = parts.pop()
  return e.key.toLowerCase() === key && e.ctrlKey === parts.includes('ctrl') &&
    e.shiftKey === parts.includes('shift') && e.altKey === parts.includes('alt')
}

// `enabled` false binds nothing (the shortcut then does not even have its default prevented).
export function useHotkey(combo: string, fn: () => void, enabled = true): void {
  useEffect(() => {
    if (!enabled) return
    const h = (e: KeyboardEvent) => { if (matches(e, combo)) { e.preventDefault(); fn() } }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [combo, fn, enabled])
}
