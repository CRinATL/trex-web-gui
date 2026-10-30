import { useState, type ReactNode } from 'react'

export const PANELS_KEY = 'trex-web-gui.monitorPanels'

// The collapsed panels, id -> true, per browser. Storage can be missing, full or hold anything: a read that fails or does not
// parse as an object of booleans is "nothing collapsed", and a write that fails is dropped.
function readCollapsed(): Record<string, boolean> {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(PANELS_KEY) ?? '{}')
    return typeof v === 'object' && v !== null && !Array.isArray(v) ? Object.fromEntries(Object.entries(v).filter(([, x]) => x === true)) : {}
  } catch { return {} }
}
function writeCollapsed(id: string, collapsed: boolean) {
  try { localStorage.setItem(PANELS_KEY, JSON.stringify({ ...readCollapsed(), [id]: collapsed })) } catch { /* private mode or a full quota: the panel still collapses for this visit */ }
}

// One panel of the Monitor grid. `present` is false until the message that feeds it has arrived (spec 6.3): the panel is then not
// shown at all. A collapsed panel keeps its children mounted (hidden), so a chart is not rebuilt on each expand.
export function MonitorPanel({ id, title, present, children }: { id: string; title: string; present: boolean; children: ReactNode }) {
  const [collapsed, setCollapsed] = useState(() => readCollapsed()[id] === true)
  if (!present) return null
  const toggle = () => { writeCollapsed(id, !collapsed); setCollapsed(!collapsed) }
  return (
    <section className="monitor-panel" aria-labelledby={`monitor-panel-${id}`}>
      <h2 id={`monitor-panel-${id}`}>
        <button type="button" className="panel-toggle" aria-expanded={!collapsed} aria-controls={`monitor-panel-body-${id}`} onClick={toggle}>
          <span aria-hidden="true">{collapsed ? '▸' : '▾'}</span> {title}
        </button>
      </h2>
      <div id={`monitor-panel-body-${id}`} hidden={collapsed}>{children}</div>
    </section>
  )
}
