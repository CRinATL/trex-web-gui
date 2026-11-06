import { useEffect, useRef, useState } from 'react'
import { ApiError, encodeOptions, type DecodedOptions, type PacketDoc, type OptionKind, type OptionKinds, type OptionRow, type OptionSpec } from '../../api'
import type { CombinedField } from './packetModel'
import { decodeBase64, encodeBase64 } from './hexModel'

const MAX_BYTES = 40
export type Machine = { vtype: 'MACHINE'; base64: string }
type Parsed<T> = { ok: true; value: T } | { ok: false; message: string }

const tooLongText = (n: number) => `options are ${n} bytes; the header holds ${MAX_BYTES} at most`

const base64Of = (v: unknown): string | null =>
  typeof v === 'object' && v !== null && 'vtype' in v && v.vtype === 'MACHINE' ? String((v as { base64?: string }).base64 ?? '') : null
const hexOf = (b64: string) => Array.from(decodeBase64(b64), (b) => b.toString(16).padStart(2, '0')).join(' ')

// Typed hex as bytes: spaces are allowed; an odd digit count, a character that is not a hex digit, or more than 40 bytes is refused.
function bytesOfHex(text: string): Parsed<Uint8Array> {
  const t = text.replace(/\s+/g, '')
  if (!/^[0-9a-f]*$/i.test(t)) return { ok: false, message: 'hex has a character that is not 0-9 or a-f' }
  if (t.length % 2 !== 0) return { ok: false, message: 'hex needs an even number of digits' }
  if (t.length / 2 > MAX_BYTES) return { ok: false, message: tooLongText(t.length / 2) }
  return { ok: true, value: Uint8Array.from(t.match(/../g) ?? [], (h) => parseInt(h, 16)) }
}

function intOf(text: string): number | null {
  const t = text.trim()
  if (/^0x[\da-f]+$/i.test(t)) return parseInt(t, 16)
  return /^\d+$/.test(t) ? Number(t) : null
}
const listOf = (text: string) => text.split(/[\s,]+/).filter(Boolean)
const quad = (s: string) => /^(\d{1,3})(\.\d{1,3}){3}$/.test(s) && s.split('.').every((p) => Number(p) <= 255)

// What one field's text commits as, by its kind; the codec checks again on the backend.
function parseField(spec: OptionSpec, text: string): Parsed<unknown> {
  const t = text.trim()
  switch (spec.kind) {
    case 'int': case 'enum': {
      let n: number | null = spec.kind === 'enum' && Object.hasOwn(spec.values ?? {}, t) ? (spec.values as Record<string, number>)[t] : intOf(t)
      if (n === null) return { ok: false, message: `${spec.name}: ${spec.kind === 'enum' ? 'not a name or a number' : 'not a number'}` }
      if ((spec.min !== undefined && n < spec.min) || (spec.max !== undefined && n > spec.max)) n = null
      return n === null ? { ok: false, message: `${spec.name}: must be from ${spec.min} to ${spec.max}` } : { ok: true, value: n }
    }
    case 'address': return quad(t) ? { ok: true, value: t } : { ok: false, message: `${spec.name}: not a dotted address` }
    case 'addresses': {
      const l = listOf(t)
      if (!l.every(quad)) return { ok: false, message: `${spec.name}: not a list of dotted addresses` }
      return spec.max_count !== undefined && l.length > spec.max_count ? { ok: false, message: `${spec.name}: at most ${spec.max_count} addresses` } : { ok: true, value: l }
    }
    case 'ints': {
      const l = listOf(t).map(intOf)
      return l.every((n) => n !== null) ? { ok: true, value: l } : { ok: false, message: `${spec.name}: not a list of numbers` }
    }
    case 'hex': {
      const b = bytesOfHex(t)
      if (!b.ok) return { ok: false, message: `${spec.name}: ${b.message}` }
      return spec.size !== undefined && b.value.length !== spec.size ? { ok: false, message: `${spec.name}: needs ${spec.size} bytes` } : { ok: true, value: t.replace(/\s+/g, '') }
    }
  }
}

// A field's value as the user reads it.
function textOfField(spec: OptionSpec, v: unknown): string {
  if (v === undefined) v = spec.default
  if (spec.kind === 'enum') {
    const label = Object.entries(spec.values ?? {}).find(([, n]) => n === v)
    return label ? label[0] : String(v)
  }
  return Array.isArray(v) ? v.join(', ') : String(v ?? '')
}

// A text input whose draft lasts while the shown value is the one it started from; Enter or leaving it commits a change.
function OptionInput({ label, base, disabled, list, onCommit }: { label: string; base: string; disabled: boolean; list?: string; onCommit: (text: string) => void }) {
  const [draft, setDraft] = useState<{ text: string; base: string } | null>(null)
  const text = draft !== null && draft.base === base ? draft.text : base
  const finish = () => { if (text !== base) { setDraft(null); onCommit(text) } }
  return <input type="text" className="field-input option-input" aria-label={label} list={list} value={text} disabled={disabled} spellCheck={false}
    onChange={(e) => setDraft({ text: e.target.value, base })}
    onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); finish() } else if (e.key === 'Escape' && draft !== null) { e.stopPropagation(); setDraft(null) } }}
    onBlur={finish} />
}

export interface OptionsEditorProps {
  field: CombinedField                    // `field.options` is set; its `layer` names the family
  kinds: OptionKinds | undefined
  pending: boolean                        // a build is in flight or waiting
  epoch: number                           // changes when the editor gets a different packet
  doc?: PacketDoc                         // the editor's current document: its identity changes on every edit, undo, redo and reset
  onCommit: (value: Machine) => void
}

// IP or TCP options as named rows in the layer: each row's fields, move and remove, Add, and hex as the fallback. An edit
// asks the backend for the bytes and commits them as MACHINE; the controls are off while the tree lags the document.
export function OptionsEditor({ field, kinds, pending, epoch, doc, onCommit }: OptionsEditorProps) {
  const opts = field.options as DecodedOptions
  const family: OptionKind[] = kinds?.[opts.layer] ?? []
  const docB64 = base64Of(field.docValue) ?? ''   // the document's bytes ('' when it sets none)
  const treeB64 = base64Of(field.value) ?? ''
  const lag = treeB64 !== docB64
  const failed = field.error !== null
  const [busy, setBusy] = useState(false)
  const [hexMode, setHexMode] = useState(false)
  const [hexDraft, setHexDraft] = useState<{ text: string; base: string } | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [addKind, setAddKind] = useState('')
  const [otherNumber, setOtherNumber] = useState('')
  const [otherHex, setOtherHex] = useState('')
  // What a reply to an earlier request is checked against: the field's document value and the epoch now, and whether this is still mounted.
  const now = useRef({ docB64, epoch, doc })
  const mounted = useRef(true)
  useEffect(() => { now.current.docB64 = docB64; now.current.epoch = epoch; now.current.doc = doc }, [docB64, epoch, doc])
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])

  const rowsOff = pending || lag || failed || busy || opts.problem !== null
  const hexOff = pending || busy || (lag && !failed)
  const hexShown = hexMode || opts.problem !== null || failed
  const hexBase = hexOf(docB64)
  // After a failed build `field.shown` is the document's raw JSON: show its bytes instead.
  const summary = failed ? (docB64 === '' ? 'none' : hexBase) : opts.rows.length === 0 && opts.problem === null ? 'none' : field.shown
  const hexText = hexDraft !== null && hexDraft.base === hexBase ? hexDraft.text : hexBase

  // Asks for the bytes of `rows` and commits them; true when they were committed. The reply is dropped unless the whole
  // document is still the one the request was made from (an undo, a redo, a layer move or another edit changes it).
  async function submit(rows: OptionRow[]): Promise<boolean> {
    const sent = { docB64, epoch, doc }
    const current = () => now.current.docB64 === sent.docB64 && now.current.epoch === sent.epoch && now.current.doc === sent.doc
    setMessage(null)
    setBusy(true)
    try {
      const r = await encodeOptions(opts.layer, rows, opts.padding, opts.length)
      if (!mounted.current) return false
      if (!current()) { setMessage('The packet changed while the options edit was being made: it was dropped.'); return false }
      onCommit({ vtype: 'MACHINE', base64: r.binary })
      return true
    } catch (e) {
      if (mounted.current && current()) setMessage(e instanceof ApiError || e instanceof Error ? e.message : String(e))
      return false
    } finally {
      if (mounted.current) setBusy(false)
    }
  }
  const change = (i: number, spec: OptionSpec, text: string) => {
    const p = parseField(spec, text)
    if (!p.ok) { setMessage(p.message); return }
    void submit(opts.rows.map((r, j) => (j === i ? { ...r, fields: { ...r.fields, [spec.name]: p.value }, edited: true } : r)))
  }
  const changeOther = (i: number, key: 'number' | 'hex', text: string) => {
    const r = opts.rows[i]
    if (key === 'number') {
      const n = intOf(text)
      if (n === null || n > 255) { setMessage('number: must be from 0 to 255'); return }
      void submit(opts.rows.map((x, j) => (j === i ? { ...x, number: n, edited: true } : x)))
    } else {
      const b = bytesOfHex(text)
      if (!b.ok) { setMessage(`value: ${b.message}`); return }
      void submit(opts.rows.map((x, j) => (j === i ? { ...x, fields: { ...r.fields, value: text.replace(/\s+/g, '') }, edited: true } : x)))
    }
  }
  const move = (i: number, dir: -1 | 1) => {
    const rows = [...opts.rows]
    ;[rows[i], rows[i + dir]] = [rows[i + dir], rows[i]]
    void submit(rows)
  }
  function add() {
    if (addKind === '') return
    if (addKind === '*other') {
      const n = intOf(otherNumber)
      if (n === null || n > 255) { setMessage('number: must be from 0 to 255'); return }
      const b = bytesOfHex(otherHex)
      if (!b.ok) { setMessage(`value: ${b.message}`); return }
      void submit([...opts.rows, { name: null, number: n, fields: { value: otherHex.replace(/\s+/g, '') }, edited: true }]).then((ok) => {
        if (ok && mounted.current) { setOtherNumber(''); setOtherHex(''); setAddKind('') }   // a refusal keeps what was typed
      })
      return
    } else {
      const kind = family.find((k) => k.name === addKind)
      if (!kind) return
      void submit([...opts.rows, { name: kind.name, number: kind.number, fields: Object.fromEntries(kind.fields.map((s) => [s.name, s.default])), edited: true }])
    }
    setAddKind('')
  }
  function commitHex() {
    const b = bytesOfHex(hexText)
    if (!b.ok) { setMessage(b.message); return }
    setMessage(null)
    setHexDraft(null)
    onCommit({ vtype: 'MACHINE', base64: encodeBase64(b.value) })
  }

  return (
    <div className="options-editor">
      <span className="field-value">{summary}</span>
      {opts.problem !== null && <p className="field-error" role="alert"><span className="mark-error" aria-hidden="true">!</span> {opts.problem}</p>}
      {hexShown ? (
        <div className="options-line">
          <input type="text" className="field-input options-hex" aria-label="Options as hex" value={hexText} disabled={hexOff} spellCheck={false}
            onChange={(e) => { setMessage(null); setHexDraft({ text: e.target.value, base: hexBase }) }}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); commitHex() } }} />
          <button type="button" disabled={hexOff} onClick={commitHex}>Apply hex</button>
          {opts.problem === null && !failed && <button type="button" onClick={() => { setHexMode(false); setHexDraft(null); setMessage(null) }}>Edit as options</button>}
        </div>
      ) : (
        <>
          {opts.rows.map((row, i) => {
            const kind = row.name === null ? null : family.find((k) => k.name === row.name) ?? null
            return (
              <div className="option-row" key={i}>
                <span className="option-name">{row.name ?? 'other'}</span>
                {row.name === null ? <>
                  <OptionInput label={`option ${i + 1} number`} base={String(row.number)} disabled={rowsOff} onCommit={(t) => changeOther(i, 'number', t)} />
                  <OptionInput label={`option ${i + 1} hex`} base={String(row.fields.value ?? '')} disabled={rowsOff} onCommit={(t) => changeOther(i, 'hex', t)} />
                </> : kind?.fields.map((spec) => {
                  const list = spec.kind === 'enum' ? `opt-${field.id}-${i}-${spec.name}` : undefined
                  return (
                    <label key={spec.name} className="option-field"><span className="muted">{spec.name}</span>
                      <OptionInput label={`option ${i + 1} ${spec.name}`} base={textOfField(spec, row.fields[spec.name])} disabled={rowsOff} list={list} onCommit={(t) => change(i, spec, t)} />
                      {list && <datalist id={list}>{Object.keys(spec.values ?? {}).map((k) => <option key={k} value={k} />)}</datalist>}
                    </label>
                  )
                })}
                <button type="button" aria-label={`Move option ${i + 1} up`} disabled={rowsOff || i === 0} onClick={() => move(i, -1)}>↑</button>
                <button type="button" aria-label={`Move option ${i + 1} down`} disabled={rowsOff || i === opts.rows.length - 1} onClick={() => move(i, 1)}>↓</button>
                <button type="button" aria-label={`Remove option ${i + 1}`} disabled={rowsOff} onClick={() => void submit(opts.rows.filter((_, j) => j !== i))}>×</button>
              </div>
            )
          })}
          <div className="option-padding muted">padding {opts.padding} byte{opts.padding === 1 ? '' : 's'}</div>
          <div className="options-line">
            <select aria-label="Add option" value={addKind} disabled={rowsOff} onChange={(e) => setAddKind(e.target.value)}>
              <option value="">Add option…</option>
              {family.map((k) => <option key={k.name} value={k.name}>{k.name}</option>)}
              <option value="*other">Other</option>
            </select>
            {addKind === '*other' && <>
              <input type="text" className="field-input option-input" aria-label="Other option number" placeholder="number" value={otherNumber} disabled={rowsOff} onChange={(e) => setOtherNumber(e.target.value)} />
              <input type="text" className="field-input option-input" aria-label="Other option hex" placeholder="hex" value={otherHex} disabled={rowsOff} onChange={(e) => setOtherHex(e.target.value)} />
            </>}
            <button type="button" disabled={rowsOff || addKind === ''} onClick={add}>Add</button>
            <button type="button" disabled={hexOff} onClick={() => { setHexMode(true); setHexDraft(null); setMessage(null) }}>Edit as hex</button>
            <span className="muted">at most {MAX_BYTES} bytes</span>
          </div>
        </>
      )}
      {message !== null && <p className="field-error" role="alert">{message}</p>}
    </div>
  )
}
