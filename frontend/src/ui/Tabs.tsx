import { useRef, type ReactNode } from 'react'

interface TabsProps<T extends string> {
  label: string                       // the tablist's accessible name
  idPrefix: string                    // ids become <idPrefix>-tab-<id> and <idPrefix>-panel-<id>
  tabs: { id: T; label: ReactNode }[]
  selected: T
  onSelect: (id: T) => void
  orientation?: 'horizontal' | 'vertical'
  end?: ReactNode                     // rendered after the tablist, outside it, pushed to the far edge
}

// An ARIA tab strip with a roving tabindex. Arrow keys (Up/Down when vertical), Home and End move selection and focus.
export function Tabs<T extends string>({ label, idPrefix, tabs, selected, onSelect, orientation = 'horizontal', end }: TabsProps<T>) {
  const refs = useRef(new Map<T, HTMLButtonElement | null>())
  const [prev, next] = orientation === 'vertical' ? ['ArrowUp', 'ArrowDown'] : ['ArrowLeft', 'ArrowRight']

  // Moves selection and focus to the tab the key names; any other key is left alone.
  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key !== prev && e.key !== next && e.key !== 'Home' && e.key !== 'End') return
    e.preventDefault()
    const i = tabs.findIndex((t) => t.id === selected)
    const to = e.key === 'Home' ? 0 : e.key === 'End' ? tabs.length - 1 : (i + (e.key === next ? 1 : tabs.length - 1)) % tabs.length
    onSelect(tabs[to].id); refs.current.get(tabs[to].id)?.focus()
  }

  return (
    <div className="tabs-row">
      <div role="tablist" aria-label={label} aria-orientation={orientation} className={`tabs tabs-${orientation}`} onKeyDown={onKeyDown}>
        {tabs.map((t) => (
          <button key={t.id} role="tab" type="button" id={`${idPrefix}-tab-${t.id}`} aria-selected={selected === t.id} aria-controls={`${idPrefix}-panel-${t.id}`} tabIndex={selected === t.id ? 0 : -1}
            ref={(el) => { refs.current.set(t.id, el) }} onClick={() => onSelect(t.id)}>{t.label}</button>
        ))}
      </div>
      {end !== undefined && <div className="tabs-end">{end}</div>}
    </div>
  )
}

// The props that tie a panel to its tab; spread onto the panel element.
// eslint-disable-next-line react-refresh/only-export-components
export function tabPanel<T extends string>(idPrefix: string, id: T, selected: T): { role: 'tabpanel'; id: string; 'aria-labelledby': string; hidden: boolean } {
  return { role: 'tabpanel', id: `${idPrefix}-panel-${id}`, 'aria-labelledby': `${idPrefix}-tab-${id}`, hidden: selected !== id }
}
