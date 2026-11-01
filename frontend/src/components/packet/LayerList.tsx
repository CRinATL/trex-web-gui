import { useState } from 'react'
import type { FeTemplateMeta, FieldValue } from '../../api'
import type { MenuAction } from '../../portActions'
import { ContextMenu } from '../ContextMenu'
import { FieldRow } from './FieldRow'
import type { Range } from './hexModel'
import type { CombinedLayer } from './packetModel'

export interface LayerListProps {
  layers: CombinedLayer[]
  expanded: ReadonlySet<number>
  onToggle: (index: number) => void
  onMove: (index: number, dir: -1 | 1) => void
  onDelete: (index: number) => void
  readOnly?: boolean
  onCommit?: (layer: number, field: string, value: FieldValue) => void
  onReset?: (layer: number, field: string) => void
  onGenerate?: (layer: number, field: string) => void
  onFeTemplate?: (layer: number, field: string, templateId: string) => void
  feTemplates?: FeTemplateMeta[]
  selected?: Range | null                      // the marked bytes; a field whose range is exactly these shows as selected
  onSelectRange?: (range: Range) => void
}

// The title says how the bytes decode when that is not the layer itself (B2.2).
const titleOf = (l: CombinedLayer) => (l.realId === null ? `${l.id} (not decoded)` : l.realId !== l.id ? `${l.id} as ${l.realId}` : l.id)

// One collapsible pane per layer. Which panes are open is the caller's, so a rebuild never changes it.
export function LayerList({ layers, expanded, onToggle, onMove, onDelete, readOnly = false, onCommit, onReset, onGenerate, onFeTemplate, feTemplates, selected = null, onSelectRange }: LayerListProps) {
  const [menu, setMenu] = useState<{ index: number; x: number; y: number } | null>(null)
  const actionsFor = (i: number): MenuAction[] => [
    { id: 'up', label: 'Move up', enabled: i > 1, run: async () => onMove(i, -1) },
    { id: 'down', label: 'Move down', enabled: i >= 1 && i < layers.length - 1, run: async () => onMove(i, 1) },
    { id: 'delete', label: 'Delete', enabled: i >= 1, run: async () => onDelete(i) },   // the first layer is fixed
  ]
  // A pane's key is its id and which of that id it is, not its index: a delete or a move keeps each pane's state with its layer.
  const seen = new Map<string, number>()
  const keys = layers.map((l) => { const n = seen.get(l.id) ?? 0; seen.set(l.id, n + 1); return `${l.id}#${n}` })
  return (
    <div className="layer-list">
      {layers.map((l, i) => {
        const open = expanded.has(i)
        return (
          <section key={keys[i]} className={`layer-pane${l.validStructure ? '' : ' invalid'}`} data-layer={i}>
            <div className="layer-head" onContextMenu={readOnly ? undefined : (e) => { e.preventDefault(); setMenu({ index: i, x: e.clientX, y: e.clientY }) }}>
              <button type="button" className="layer-title" aria-expanded={open} onClick={() => onToggle(i)}>
                <span aria-hidden="true">{open ? '▾' : '▸'}</span> {titleOf(l)}
                {!l.validStructure && <> <span className="mark-error" role="img" aria-label="invalid structure">!</span></>}
              </button>
              {!readOnly && (
                <button type="button" className="layer-menu" aria-label={`Layer ${i + 1} menu`} onClick={(e) => {
                  const r = e.currentTarget.getBoundingClientRect()
                  setMenu({ index: i, x: r.left, y: r.bottom })
                }}>⋮</button>
              )}
            </div>
            {open && (
              <div className="layer-body">
                {l.error !== null && <p className="field-error" role="alert"><span className="mark-error" aria-hidden="true">!</span> {l.error}</p>}
                {l.fields.map((f) => (
                  <FieldRow key={f.id} field={f} readOnly={readOnly} feTemplates={feTemplates} onSelectRange={onSelectRange}
                    selected={selected !== null && f.range !== null && f.range[0] === selected[0] && f.range[1] === selected[1]}
                    onCommit={(v) => onCommit?.(i, f.id, v)} onReset={() => onReset?.(i, f.id)}
                    onGenerate={onGenerate ? () => onGenerate(i, f.id) : undefined}
                    onFeTemplate={onFeTemplate ? (t) => onFeTemplate(i, f.id, t) : undefined} />
                ))}
              </div>
            )}
          </section>
        )
      })}
      {menu && <ContextMenu x={menu.x} y={menu.y} actions={actionsFor(menu.index)} onClose={() => setMenu(null)} onError={() => undefined} />}
    </div>
  )
}
