import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { buildPacket, packetDefinitions, reconstructEdit, type Definitions, type FieldValue, type PacketDoc, type PacketTree } from '../../api'
import { AppendLayer } from './AppendLayer'
import { Breadcrumb } from './Breadcrumb'
import { FieldEngine } from './FieldEngine'
import { fromTemplate } from './feModel'
import { decodeBase64, encodeBase64, type Range } from './hexModel'
import { HexView } from './HexView'
import { LayerList } from './LayerList'
import { appendLayer, combine, deleteLayer, fieldAt, layersOfTree, moveLayer, resetField, setField, type CombinedLayer } from './packetModel'
import { useEditor } from './useEditor'

export type EditorMode = 'standalone' | 'embedded' | 'viewer'
export interface ExtraTab { id: string; label: string; content: ReactNode }
export interface PacketEditorProps {
  doc: PacketDoc
  onDocChange: (doc: PacketDoc) => void
  mode: EditorMode
  extraTabs?: ExtraTab[]
  tree?: PacketTree          // viewer mode: the decoded packet to show
  onOpen?: () => void        // Ctrl+O, standalone only
  onSave?: () => void        // Ctrl+S, standalone only
}

// The definitions, fetched once per editor.
function useDefinitions(): { defs: Definitions | null; error: string | null } {
  const [state, setState] = useState<{ defs: Definitions | null; error: string | null }>({ defs: null, error: null })
  useEffect(() => {
    let live = true
    packetDefinitions().then(
      (defs) => { if (live) setState({ defs, error: null }) },
      (e: unknown) => { if (live) setState({ defs: null, error: e instanceof Error ? e.message : String(e) }) })
    return () => { live = false }
  }, [])
  return state
}

// The open panes follow their layer when it moves or goes.
const afterMove = (open: ReadonlySet<number>, i: number, to: number) => new Set([...open].map((k) => (k === i ? to : k === to ? i : k)))
const afterDelete = (open: ReadonlySet<number>, i: number) => new Set([...open].filter((k) => k !== i).map((k) => (k > i ? k - 1 : k)))

const lengthOf = (binary: string | null | undefined) => (binary ? decodeBase64(binary).length : 0)

function Tabs({ tabs, id, onSelect }: { tabs: ExtraTab[]; id: string; onSelect: (id: string) => void }) {
  const setId = onSelect
  const current = tabs.find((t) => t.id === id) ?? tabs[0]
  return (
    <>
      <div role="tablist" className="editor-tabs">
        {tabs.map((t) => <button key={t.id} type="button" role="tab" aria-selected={t.id === current.id} onClick={() => setId(t.id)}>{t.label}</button>)}
      </div>
      <div role="tabpanel">{current.content}</div>
    </>
  )
}

function Toolbar({ children, onExpandAll, onCollapseAll }: { children?: ReactNode; onExpandAll: () => void; onCollapseAll: () => void }) {
  return (
    <div className="editor-toolbar" role="toolbar" aria-label="Packet editor">
      {children}
      <button type="button" onClick={onExpandAll}>Expand all</button>
      <button type="button" onClick={onCollapseAll}>Collapse all</button>
    </div>
  )
}

const allOf = (layers: CombinedLayer[]) => new Set(layers.map((l) => l.index))

// A capture's packet: its layers, read-only, no definitions and no build.
function Viewer({ tree }: { tree: PacketTree | undefined }) {
  const layers = tree ? layersOfTree(tree) : []
  const [open, setOpen] = useState<ReadonlySet<number>>(() => new Set([0]))
  const toggle = (i: number) => setOpen((o) => { const n = new Set(o); if (!n.delete(i)) n.add(i); return n })
  return (
    <div className="packet-editor viewer">
      <Toolbar onExpandAll={() => setOpen(allOf(layers))} onCollapseAll={() => setOpen(new Set())} />
      <Breadcrumb layers={layers} length={lengthOf(tree?.binary)} onSelect={(i) => setOpen((o) => new Set(o).add(i))} />
      <LayerList layers={layers} expanded={open} onToggle={toggle} onMove={() => undefined} onDelete={() => undefined} readOnly />
    </div>
  )
}

function Editable({ doc, onDocChange, mode, extraTabs = [], onOpen, onSave }: Omit<PacketEditorProps, 'tree'>) {
  const ed = useEditor(doc, { build: buildPacket })
  const { defs, error: defsError } = useDefinitions()
  const [open, setOpen] = useState<ReadonlySet<number>>(() => new Set([0]))
  const [tab, setTab] = useState('packet')
  const [active, setActive] = useState<number | null>(null)   // the layer opened last: the one Ctrl+D deletes
  const [marked, setMarked] = useState<Range | null>(null)
  const [status, setStatus] = useState<{ kind: 'status' | 'error'; text: string } | null>(null)
  const current = useRef(ed.doc)
  useEffect(() => { current.current = ed.doc }, [ed.doc])

  // The parent's document follows the editor's; a different document from the parent replaces the editor's.
  const seen = useRef(doc)   // the document the parent and the editor last agreed on
  const prop = useRef(doc)   // the parent's document as of the last render
  const { reset } = ed
  useEffect(() => {
    if (doc === prop.current) return
    prop.current = doc
    if (doc !== seen.current) { seen.current = doc; reset(doc) }   // not the echo of an edit
  }, [doc, reset])
  useEffect(() => { if (ed.doc !== seen.current) { seen.current = ed.doc; onDocChange(ed.doc) } }, [ed.doc, onDocChange])

  const layers = defs ? combine(ed.doc, ed.lastGood, defs, ed.error) : []
  const expand = (i: number) => { setOpen((o) => new Set(o).add(i)); setActive(i) }
  const toggle = (i: number) => {
    if (open.has(i)) { setOpen((o) => { const n = new Set(o); n.delete(i); return n }); setActive((a) => (a === i ? null : a)) } else expand(i)
  }
  const del = (i: number) => {
    if (i < 1 || i >= ed.doc.packet.length) return
    ed.apply((d) => deleteLayer(d, i), `delete ${ed.doc.packet[i].id}`)
    setOpen((o) => afterDelete(o, i)); setActive(null)
  }
  const move = (i: number, dir: -1 | 1) => { ed.apply((d) => moveLayer(d, i, dir), 'move layer'); setOpen((o) => afterMove(o, i, i + dir)); setActive((a) => (a === i ? i + dir : a === i + dir ? i : a)) }
  const append = (id: string) => { const at = ed.doc.packet.length; ed.apply((d) => appendLayer(d, id), `add ${id}`); setOpen(new Set([at])); setActive(at) }   // the others collapse (B1.10)

  // A Field Engine template on a field adds its instructions and shows them.
  const feTemplate = (layer: number, field: string, templateId: string) => {
    if (!defs) return
    ed.apply((d) => fromTemplate(d, templateId, d.packet[layer]?.id ?? '', field, defs, layer), `field engine ${templateId}`)
    setTab('fe')
  }

  const bytes = ed.lastGood?.binary ? decodeBase64(ed.lastGood.binary) : null
  const selectByte = (i: number) => setMarked(fieldAt(layers, i)?.range ?? [i, i + 1])

  // A byte edit goes through reconstruct with the field that holds the byte (D12); the doc it returns is one undo step.
  async function editByte(i: number, value: number) {
    if (bytes === null || i >= bytes.length) return
    if (ed.pending || (ed.error !== null && ed.error.kind !== 'fe')) { setStatus({ kind: 'error', text: 'The packet is not built yet: try the byte edit again when it is.' }); return }
    const sent = ed.doc
    const hit = fieldAt(layers, i)
    const next = Uint8Array.from(bytes)
    next[i] = value
    try {
      const r = await reconstructEdit(sent, encodeBase64(next), hit ? { layer: hit.layer, field: hit.field } : null)
      if (current.current !== sent) { setStatus({ kind: 'error', text: 'The packet changed while the byte edit was being made: it was dropped.' }); return }
      ed.apply(() => r.doc, 'edit byte')
      setStatus(r.warnings.length > 0 ? { kind: 'status', text: r.warnings.join(' ') } : null)
    } catch (e) {
      setStatus({ kind: 'error', text: `Byte edit failed: ${e instanceof Error ? e.message : String(e)}` })
    }
  }

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (!(e.ctrlKey || e.metaKey) || e.altKey) return
    const k = e.key.toLowerCase()
    let handled = true
    if (k === 'z' && !e.shiftKey) ed.undo()
    else if ((k === 'z' && e.shiftKey) || k === 'y' || k === 'r') ed.redo()
    else if (k === 'd') { if (tab === 'packet' && active !== null && open.has(active)) del(active) }   // the expanded layer you opened last; del refuses the first
    else if (k === 'o' && mode === 'standalone' && onOpen) onOpen()
    else if (k === 's' && mode === 'standalone' && onSave) onSave()
    else handled = false
    if (handled) e.preventDefault()
  }

  const fe = ed.error !== null && ed.error.field === null ? ed.error : null
  const packetTab = (
    <div className="packet-tab">
      <Breadcrumb layers={layers} length={lengthOf(ed.lastGood?.binary)} onSelect={expand} />
      {fe && <p className="field-error" role="alert"><span className="mark-error" aria-hidden="true">!</span> {fe.message}</p>}
      {defs && <LayerList layers={layers} expanded={open} onToggle={toggle} onMove={move} onDelete={del}
        onCommit={(l, f, v: FieldValue) => ed.apply((d) => setField(d, l, f, v), `set ${f}`)}
        onReset={(l, f) => ed.apply((d) => resetField(d, l, f), `reset ${f}`)}
        onGenerate={(l, f) => ed.apply((d) => setField(d, l, f, { vtype: 'RANDOM' }), `generate ${f}`)}
        feTemplates={defs.feTemplates} onFeTemplate={feTemplate} selected={marked} onSelectRange={setMarked} />}
      {defs && <AppendLayer doc={ed.doc} defs={defs} onAppend={append} />}
      {defs && bytes !== null && <HexView bytes={bytes} selected={marked} onSelectByte={selectByte} onEditByte={(i, v) => void editByte(i, v)} />}
      {status && <p role={status.kind === 'error' ? 'alert' : 'status'} className={status.kind === 'error' ? 'field-error' : 'muted'}>{status.text}</p>}
      {!defs && defsError === null && <p className="muted">Loading definitions…</p>}
      {defsError !== null && <p className="field-error" role="alert"><span className="mark-error" aria-hidden="true">!</span> {defsError}</p>}
    </div>
  )
  const tabs: ExtraTab[] = [
    { id: 'packet', label: 'Packet', content: packetTab },
    { id: 'fe', label: 'Field engine', content: defs
      ? <FieldEngine doc={ed.doc} result={ed.result} defs={defs} onApply={ed.apply} rejected={ed.rejected} error={ed.error} />
      : <p className="muted">Loading definitions…</p> },
    ...extraTabs,
  ]
  return (
    <div className="packet-editor" onKeyDown={onKeyDown}>
      <Toolbar onExpandAll={() => { setOpen(allOf(layers)); setActive(null) }} onCollapseAll={() => { setOpen(new Set()); setActive(null) }}>
        <button type="button" disabled={!ed.canUndo} onClick={ed.undo}>Undo</button>
        <button type="button" disabled={!ed.canRedo} onClick={ed.redo}>Redo</button>
      </Toolbar>
      <Tabs tabs={tabs} id={tab} onSelect={setTab} />
    </div>
  )
}

// The packet editor: layers as panes of field rows over the user's document. `viewer` shows a decoded packet read-only.
export function PacketEditor(props: PacketEditorProps) {
  return props.mode === 'viewer' ? <Viewer tree={props.tree} /> : <Editable {...props} />
}
