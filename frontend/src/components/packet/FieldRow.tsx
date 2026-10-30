import { useId, useState, type KeyboardEvent, type MouseEvent } from 'react'
import type { FeTemplateMeta, FieldDef, FieldValue } from '../../api'
import type { MenuAction } from '../../portActions'
import { ContextMenu } from '../ContextMenu'
import { BitFlags } from './BitFlags'
import { rangeText } from './hexModel'
import { OptionsRow } from './OptionsRow'
import { PayloadEditor } from './PayloadEditor'
import type { CombinedField } from './packetModel'

type Parsed = { ok: true; value: FieldValue } | { ok: false; message: string }

// A number as typed: decimal (a fraction too) or 0x hex; null for anything else.
function numberOf(text: string): number | null {
  if (/^0x[\da-f]+$/i.test(text)) return parseInt(text, 16)
  return /^[+-]?\d+(\.\d+)?$/.test(text) ? Number(text) : null
}
const rangeMessage = (def: FieldDef) =>
  def.min !== undefined && def.max !== undefined ? `must be from ${def.min} to ${def.max}` : def.min !== undefined ? `must be at least ${def.min}` : `must be at most ${def.max}`
const outside = (def: FieldDef, n: number) => (def.min !== undefined && n < def.min) || (def.max !== undefined && n > def.max)

// An ENUM value is committed as its number (R13): a name is mapped here, from the definitions the host sent (R7), so the
// document never holds a name that another host's tables might not have.
function enumNumber(def: FieldDef, text: string): number | null {
  const dict = def.values_dict ?? {}
  if (Object.hasOwn(dict, text)) return dict[text]
  const same = Object.keys(dict).filter((k) => k.toLowerCase() === text.toLowerCase())
  if (same.length === 1) return dict[same[0]]
  const n = numberOf(text)
  return n !== null && Number.isInteger(n) ? n : null
}

// What committing `text` to a field of definition `def` would store, or why it cannot be.
function parseText(def: FieldDef, text: string): Parsed {
  const t = text.trim()
  if (def.type === 'ENUM') {
    const n = enumNumber(def, t)
    if (n === null) return { ok: false, message: 'not a name or a number' }
    return outside(def, n) ? { ok: false, message: rangeMessage(def) } : { ok: true, value: n }
  }
  if (def.type === 'NUMBER' || def.min !== undefined || def.max !== undefined) {
    const n = numberOf(t)
    if (n === null) return { ok: false, message: 'not a number' }
    if (Number.isInteger(n) && !Number.isSafeInteger(n)) return { ok: true, value: t }   // too big for a double: the service parses the text
    return outside(def, n) ? { ok: false, message: rangeMessage(def) } : { ok: true, value: n }
  }
  if (def.regex) {
    let re: RegExp | null = null
    try { re = new RegExp(def.regex) } catch { /* a Python-only pattern: the service decides */ }
    if (re && !re.test(text)) return { ok: false, message: 'not a valid value' }
  }
  return { ok: true, value: text }
}

const exprOf = (v: FieldValue | undefined): string | null =>
  typeof v === 'object' && v !== null && 'vtype' in v && v.vtype === 'EXPRESSION' ? (v.expr ?? '') : null

export interface FieldRowProps {
  field: CombinedField
  onCommit: (value: FieldValue) => void
  onReset: () => void
  onGenerate?: () => void
  onFeTemplate?: (templateId: string) => void
  feTemplates?: FeTemplateMeta[]
  readOnly?: boolean
  selected?: boolean                      // its bytes are the marked ones in the hex view
  onSelectRange?: (range: [number, number]) => void   // the byte range is a button that marks those bytes
}

// One field: its name, a control for its type, the byte range, and the user-set and error marks (each a glyph as well as a colour).
export function FieldRow({ field, onCommit, onReset, onGenerate, onFeTemplate, feTemplates = [], readOnly = false, selected = false, onSelectRange }: FieldRowProps) {
  const { def } = field
  const locked = readOnly || field.readOnly
  const [draft, setDraft] = useState<{ text: string; base: string } | null>(null)   // the user's text, for as long as the shown value is the one it started from
  const [asExpr, setAsExpr] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [payloadOpen, setPayloadOpen] = useState(false)
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null)
  const listId = useId()
  const builtExpr = exprOf(field.docValue)   // the document's value: the build turns an expression into its result
  const expr = asExpr || builtExpr !== null
  const base = builtExpr !== null && field.error === null ? builtExpr : field.shown
  const text = draft !== null && draft.base === base ? draft.text : base
  const dirty = draft !== null && draft.base === base && draft.text !== base

  function commit(raw: string) {
    const parsed: Parsed = expr ? (raw.trim() === '' ? { ok: false, message: 'enter a Python expression' } : { ok: true, value: { vtype: 'EXPRESSION', expr: raw } }) : parseText(def, raw)
    if (!parsed.ok) { setProblem(parsed.message); return }
    setProblem(null)
    setDraft(null)      // the commit is the value now: a later undo must show the document's value, not this text
    onCommit(parsed.value)
  }
  function edit(raw: string, picked: boolean) {
    setProblem(null)
    setDraft({ text: raw, base })
    // a name chosen from the list (not typed) is a complete value
    if (picked && def.type === 'ENUM' && Object.hasOwn(def.values_dict ?? {}, raw)) commit(raw)
  }
  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') { e.preventDefault(); if (dirty || field.error !== null) commit(text) }
    else if (e.key === 'Escape' && (draft !== null || problem !== null)) { e.stopPropagation(); setDraft(null); setProblem(null) }
    // While the text is being typed, undo and redo belong to the text, not the document: they revert it as Escape does
    else if (dirty && (e.ctrlKey || e.metaKey) && !e.altKey && ['z', 'y', 'r'].includes(e.key.toLowerCase())) { e.preventDefault(); e.stopPropagation(); setDraft(null); setProblem(null) }
  }
  function openMenu(e: MouseEvent) { e.preventDefault(); setMenu({ x: e.clientX, y: e.clientY }) }

  const actions: MenuAction[] = [
    { id: 'generate', label: 'Generate', enabled: onGenerate !== undefined && def.type !== 'BYTES', run: async () => onGenerate?.() },
    { id: 'default', label: 'Set to default', enabled: field.userSet, run: async () => onReset() },
    ...(def.type === undefined || def.type === 'NUMBER' || def.type === 'STRING'
      ? [{ id: 'expression', label: expr ? 'Edit as text' : 'Edit as expression', enabled: builtExpr === null, run: async () => { setAsExpr(!asExpr); setDraft(null); setProblem(null) } }]
      : []),
    ...(onFeTemplate ? feTemplates.map((t): MenuAction => ({ id: `fe-${t.id}`, label: `Field engine: ${t.name}`, enabled: true, run: async () => onFeTemplate(t.id) })) : []),
  ]

  const message = problem ?? field.error
  const range = field.range ? rangeText(field.range) : field.ignored ? 'not in packet' : 'bits'
  const cls = ['field-row', field.userSet ? 'user-set' : '', message !== null ? 'error' : '', field.ignored ? 'ignored' : ''].filter(Boolean).join(' ')

  let control
  if (locked) control = <span className="field-value">{field.shown}</span>
  else if (def.type === 'BYTES') {
    control = <>
      <span className="field-value field-bytes">{field.shown.length > 48 ? `${field.shown.slice(0, 48)}…` : field.shown}</span>
      <button type="button" onClick={() => setPayloadOpen(true)}>Payload…</button>
      {payloadOpen && <PayloadEditor value={field.docValue} onSave={(v) => { setPayloadOpen(false); onCommit(v) }} onCancel={() => setPayloadOpen(false)} />}
    </>
  } else if (def.type === 'BITMASK' && def.bits) control = <BitFlags def={def} value={typeof field.value === 'number' ? field.value : 0} onCommit={onCommit} />
  else if (def.type === 'IP_OPTIONS' || def.type === 'TCP_OPTIONS') control = <OptionsRow field={field} />
  else {
    const list = def.type === 'ENUM' && !expr ? listId : undefined
    control = <>
      <input type="text" className="field-input" aria-label={def.name} aria-invalid={message !== null} list={list} value={text} spellCheck={false}
        onChange={(e) => edit(e.target.value, (e.nativeEvent as InputEvent).inputType === undefined || (e.nativeEvent as InputEvent).inputType === 'insertReplacementText')}
        onKeyDown={onKeyDown} onFocus={(e) => e.currentTarget.select()} onClick={(e) => e.currentTarget.select()}
        onBlur={() => { if (dirty) commit(text); setDraft(null); setProblem(null) }} />
      {list && <datalist id={list}>{Object.keys(def.values_dict ?? {}).map((k) => <option key={k} value={k} />)}</datalist>}
      {expr && <span className="muted field-note">evaluated as Python on the backend</span>}
    </>
  }

  return (
    <div className={cls} data-field={field.id} onContextMenu={locked ? undefined : openMenu}>
      <span className="field-name" title={field.id}>{def.name}</span>
      <span className="field-control">{control}</span>
      <span className="field-marks">
        {field.userSet && <span className="mark-user" role="img" aria-label="user-set">•</span>}
        {message !== null && <span className="mark-error" role="img" aria-label="error">!</span>}
        {field.userSet && !locked && <button type="button" className="field-reset" aria-label={`Reset ${def.name} to default`} onClick={() => { setDraft(null); setProblem(null); onReset() }}>×</button>}
      </span>
      {field.range && onSelectRange
        ? <button type="button" className={`field-range muted${selected ? ' selected' : ''}`} aria-pressed={selected} aria-label={`${def.name} bytes ${range}`} onClick={() => field.range && onSelectRange(field.range)}>{range}</button>
        : <span className="field-range muted">{range}</span>}
      {message !== null && <span className="field-error" role="alert">{message}</span>}
      {menu && <ContextMenu x={menu.x} y={menu.y} actions={actions} onClose={() => setMenu(null)} onError={() => undefined} />}
    </div>
  )
}
