import { useState } from 'react'
import type { FeTemplateMeta, FieldValue, OptionKinds, PacketDoc } from '../../api'
import type { MenuAction } from '../../portActions'
import { ContextMenu } from '../ContextMenu'
import { FieldRow } from './FieldRow'
import type { Range } from './hexModel'
import type { CombinedLayer, StructureNote } from './packetModel'

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
  optionKinds?: OptionKinds
  pending?: boolean                            // a build is in flight or waiting: the options editor is off
  epoch?: number                               // changes when the editor gets a different packet
  doc?: PacketDoc                              // the editor's current document
}

// The title says how the bytes decode when that is not the layer itself (B2.2).
const titleOf = (l: CombinedLayer) => (l.realId === null ? `${l.id} (not decoded)` : l.realId !== l.id ? `${l.id} as ${l.realId}` : l.id)

// "UDP dport is 53, which makes UDP read the bytes after it as DNS." for the values that bind what the bytes read as.
function selectorText(note: StructureNote, realId: string | null): string {
  const s = note.selectors
  const said = s.map((f) => `${f.layerId} ${f.name} is ${f.value}`).join(' and ')
  const read = `${s[0].layerId} read the bytes after it as ${realId}`
  return s.length === 1 ? `${said}, which makes ${read}.` : `${said}; each makes ${read}, so change all of them.`
}

// Why a layer does not read back as itself, and in the editor each field set above it whose editor value would fix that,
// with a button that hands the field back to the editor (the row's own reset).
function StructureNoteView({ note, id, realId, readOnly, onReset }: { note: StructureNote; id: string; realId: string | null; readOnly: boolean; onReset?: (layer: number, field: string) => void }) {
  return (
    <div className="structure-note">
      <p className="field-error"><span className="mark-error" aria-hidden="true">!</span> {note.reason}</p>
      {note.selectors.length > 0 && <p>{selectorText(note, realId)}</p>}
      {note.fixes.map((f) => (
        <p key={`${f.layer}:${f.field}`}>
          {f.layerId} {f.name} is {f.current}; left to the editor it would be {f.value}, and this layer reads back as {id}.{' '}
          {!readOnly && onReset && <button type="button" onClick={() => onReset(f.layer, f.field)}>Reset {f.layerId} {f.name}</button>}
        </p>
      ))}
      {note.unexplained && <p className="muted">None of the fields you set above this layer changes that.</p>}
    </div>
  )
}

// One collapsible pane per layer. Which panes are open is the caller's, so a rebuild never changes it.
export function LayerList({ layers, expanded, onToggle, onMove, onDelete, readOnly = false, onCommit, onReset, onGenerate, onFeTemplate, feTemplates, selected = null, onSelectRange, optionKinds, pending = false, epoch = 0, doc }: LayerListProps) {
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
                {!l.validStructure && <> <span className="mark-error" role="img" aria-label="invalid structure" title={l.structure?.reason}>!</span></>}
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
                {l.structure !== null && <StructureNoteView note={l.structure} id={l.id} realId={l.realId} readOnly={readOnly} onReset={onReset} />}
                {l.fields.map((f) => (
                  <FieldRow key={f.id} field={f} readOnly={readOnly} feTemplates={feTemplates} onSelectRange={onSelectRange} optionKinds={optionKinds} pending={pending} epoch={epoch} doc={doc}
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
