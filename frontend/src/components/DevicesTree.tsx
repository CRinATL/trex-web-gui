import { useRef, useState } from 'react'
import type { PortView } from '../api'
import type { Selection } from '../portActions'
import { portAria, portTitle, type PortLabel } from '../portLabels'

const same = (a: Selection, b: Selection) => JSON.stringify(a) === JSON.stringify(b)

interface Row { sel: Selection; depth: number; label: string; inferred?: string; aria: string; title?: string; port?: PortView; profile?: { id: string; state: string } }

export const INFERRED_HINT = 'Interface name found on the TRex host; Save it in the port\'s Name to keep it'

function rowsFor(host: string, ports: PortView[], labelOf: (p: PortView) => PortLabel | null): Row[] {
  const rows: Row[] = [{ sel: { kind: 'root' }, depth: 0, label: `TRex-${host}`, aria: `TRex-${host}` }]
  for (const p of ports) {
    const owner = p.owned ? 'owned by you' : p.owner ? `owned by ${p.owner}` : 'not owned'
    const lab = labelOf(p)
    rows.push({ sel: { kind: 'port', id: p.id }, depth: 1, label: lab?.inferred ? `Port ${p.id}` : portTitle(p, lab),
      inferred: lab?.inferred ? lab.name : undefined, title: lab?.inferred ? (lab.description ? `${lab.description}\n${INFERRED_HINT}` : INFERRED_HINT) : lab?.description || undefined, port: p, aria: `${portAria(p, lab)}, ${p.state}, ${owner}${p.service_filtered ? ', service mode filtered' : p.service ? ', service mode on' : ''}` })
    for (const [id, state] of Object.entries(p.profiles)) {
      rows.push({ sel: { kind: 'profile', port: p.id, profile: id }, depth: 2, label: `Profile ${id} (${state})`, profile: { id, state },
        aria: `Profile ${id} (${state})${id === '_' ? '' : ', read-only'}` })
    }
  }
  return rows
}

const NO_LABEL = () => null

export function DevicesTree({ host, ports, selection, onSelect, onContextMenu, labelOf = NO_LABEL }:
  { host: string; ports: PortView[]; selection: Selection; onSelect: (s: Selection) => void; onContextMenu: (s: Selection, x: number, y: number) => void
    labelOf?: (p: PortView) => PortLabel | null }) {
  const rows = rowsFor(host, ports, labelOf)
  const refs = useRef<(HTMLDivElement | null)[]>([])
  const [focusIdx, setFocusIdx] = useState<number | null>(null)
  const selIdx = rows.findIndex((r) => same(r.sel, selection))
  // roving tabindex: the focused row, else the selected one, else the root, is the single tab stop
  const tabIdx = focusIdx !== null && focusIdx < rows.length ? focusIdx : Math.max(selIdx, 0)

  function onKeyDown(e: React.KeyboardEvent, i: number) {
    const move = (to: number) => { e.preventDefault(); const j = Math.min(Math.max(to, 0), rows.length - 1); refs.current[j]?.focus(); onSelect(rows[j].sel) }
    if (e.key === 'ArrowDown') move(i + 1)
    else if (e.key === 'ArrowUp') move(i - 1)
    else if (e.key === 'Home') move(0)
    else if (e.key === 'End') move(rows.length - 1)
    else if (e.key === 'Enter') { e.preventDefault(); onSelect(rows[i].sel) }
    else if (e.key === 'ContextMenu' || (e.key === 'F10' && e.shiftKey)) {
      e.preventDefault()
      onSelect(rows[i].sel)   // like right-click: the menu acts on the row it opened from
      const r = refs.current[i]?.getBoundingClientRect()
      onContextMenu(rows[i].sel, r?.left ?? 0, r?.bottom ?? 0)
    }
  }

  return (
    <div role="tree" aria-label="Devices" className="devices-tree">
      {rows.map((r, i) => (
        <div key={JSON.stringify(r.sel)} role="treeitem" aria-label={r.aria} title={r.title} aria-level={r.depth + 1} aria-expanded={r.port && Object.keys(r.port.profiles).length > 0 ? true : undefined} aria-selected={same(r.sel, selection)}
          ref={(el) => { refs.current[i] = el }} tabIndex={i === tabIdx ? 0 : -1}
          className={`tree-row depth-${r.depth}${r.port ? ' port-row' : ''}${same(r.sel, selection) ? ' selected' : ''}`}
          onFocus={() => setFocusIdx(i)}
          onClick={() => onSelect(r.sel)}
          onContextMenu={(e) => { e.preventDefault(); onSelect(r.sel); onContextMenu(r.sel, e.clientX, e.clientY) }}
          onKeyDown={(e) => onKeyDown(e, i)}>
          {r.port && r.inferred ? <><span>{r.label}</span><span className="muted mono">{r.inferred}</span></>
            : <span className={r.port ? 'span-name' : undefined}>{r.label}</span>}
          {r.port && <>
            <span className={`badge st-${r.port.state}`}>{r.port.state}</span>
            <span className="owner">{r.port.owned ? 'you' : r.port.owner || '—'}</span>
            {(r.port.service || r.port.service_filtered) && <span className="badge svc">SVC</span>}
          </>}
          {r.profile && r.profile.id !== '_' && <span className="muted">read-only</span>}
        </div>
      ))}
    </div>
  )
}
