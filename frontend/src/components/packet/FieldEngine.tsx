import { useId, useState } from 'react'
import { decompileVm, paramValues, vmPython, type BuildResult, type Definitions, type FeInstruction, type FeParamMeta, type PacketDoc } from '../../api'
import { decodeBase64 } from './hexModel'
import { addInstruction, deleteInstruction, ENUM_PARAMS, feText, setGlobal, setParam } from './feModel'
import { setFe, type EditorError } from './packetModel'
import type { Rejected } from './useEditor'

export interface FieldEngineProps {
  doc: PacketDoc
  result: BuildResult | null
  defs: Definitions
  onApply: (fn: (doc: PacketDoc) => PacketDoc, label: string, fe?: { key: string; value: string }) => void
  rejected?: Rejected | null  // a value the engine refused: the editor took the edit back, and the value stays beside the message (B5.3)
  error?: EditorError | null  // the editor's current failure, for the kinds that are not the FE's own
  readOnly?: boolean
}

const helpText = (b64: string): string => { try { return new TextDecoder().decode(decodeBase64(b64)) } catch { return '' } }
const paramKey = (i: number, id: string) => `${i}:${id}`
const globalKey = (id: string) => `global:${id}`

// One parameter: a text box that commits on Enter or blur. An ENUM parameter the service lists (ENUM_PARAMS) asks for its
// values when focused; one with a fixed dictionary lists it.
function ParamInput({ meta, value, rejected, readOnly, doc, onCommit, label }: {
  meta: FeParamMeta; value: string; rejected: Rejected | null; readOnly: boolean; doc: PacketDoc; onCommit: (v: string) => void; label: string
}) {
  const [draft, setDraft] = useState<string | null>(null)
  const [options, setOptions] = useState<string[] | null>(null)
  const [fetchError, setFetchError] = useState<string | null>(null)
  const listId = useId()
  const text = draft ?? rejected?.value ?? value
  const fixed = meta.dict ? Object.keys(meta.dict) : null
  const live = ENUM_PARAMS.includes(meta.id)
  const choices = live ? (options ?? []) : fixed
  async function load() {
    if (!live) return
    try { setOptions(Object.keys((await paramValues(doc, meta.id)).map)); setFetchError(null) }
    catch (e) { setFetchError(e instanceof Error ? e.message : String(e)) }
  }
  // Enter sends the text again even when it is the rejected one; blur sends only what was typed.
  function commit(retry: boolean) {
    if (draft !== null && draft !== value) onCommit(draft)
    else if (retry && draft === null && rejected !== null && rejected.value !== value) onCommit(rejected.value)
    setDraft(null)
  }
  return (
    <span className={`fe-param${rejected ? ' error' : ''}`}>
      <input type="text" className="field-input" aria-label={label} aria-invalid={rejected !== null} spellCheck={false} disabled={readOnly}
        list={choices ? listId : undefined} value={text} placeholder={meta.defaultValue === undefined ? undefined : String(meta.defaultValue)}
        onChange={(e) => setDraft(e.target.value)} onFocus={() => void load()}
        onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); commit(true) } else if (e.key === 'Escape' && draft !== null) { e.stopPropagation(); setDraft(null) } }}
        onBlur={() => commit(false)} />
      {choices && <datalist id={listId}>{choices.map((k) => <option key={k} value={k} />)}</datalist>}
      {rejected && <span className="field-error" role="alert"><span className="mark-error" aria-hidden="true">!</span> {rejected.message}</span>}
      {fetchError !== null && <span className="field-error" role="alert">{fetchError}</span>}
    </span>
  )
}

// The raw-VM import: JSON text pasted or read from a file. The instructions replace the document's.
function ImportRaw({ result, onImport, onClose }: { result: BuildResult | null; onImport: (r: { instructions: FeInstruction[]; globals: PacketDoc['fePrarameters'] }) => void; onClose: () => void }) {
  const [text, setText] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const [warnings, setWarnings] = useState<string[]>([])
  const id = useId()
  async function run() {
    setWarnings([]); setProblem(null)
    if (!result?.binary) { setProblem('The packet is not built yet: the VM is read against the packet it belongs to.'); return }
    let vm: unknown
    try { vm = JSON.parse(text) } catch (e) { setProblem(`Not JSON: ${e instanceof Error ? e.message : String(e)}`); return }
    if (typeof vm !== 'object' || vm === null || !Array.isArray((vm as { instructions?: unknown }).instructions)) { setProblem('A VM is an object with an "instructions" list.'); return }
    try {
      const d = await decompileVm(result.binary, vm as Parameters<typeof decompileVm>[1])
      onImport({ instructions: d.instructions, globals: d.global_parameters })
      setWarnings(d.warnings)
    } catch (e) { setProblem(e instanceof Error ? e.message : String(e)) }
  }
  async function pick(file: File | undefined) {
    if (!file) return
    try { setText(await file.text()); setProblem(null) } catch { setProblem(`${file.name} could not be read`) }
  }
  return (
    <div className="fe-import" role="group" aria-label="Import raw VM">
      <label htmlFor={`${id}-text`}>VM JSON</label>
      <textarea id={`${id}-text`} rows={8} spellCheck={false} value={text} onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && ['z', 'y', 'r'].includes(e.key.toLowerCase())) e.stopPropagation() }} />   {/* the text box keeps its own undo; Ctrl+S and Ctrl+O reach the editor */}
      <label htmlFor={`${id}-file`}>or a file</label>
      <input id={`${id}-file`} type="file" onChange={(e) => { void pick(e.target.files?.[0]); e.target.value = '' }} />
      <div className="row">
        <button type="button" onClick={() => void run()}>Import</button>
        <button type="button" onClick={onClose}>Close</button>
      </div>
      {problem !== null && <p className="field-error" role="alert"><span className="mark-error" aria-hidden="true">!</span> {problem}</p>}
      {warnings.length > 0 && <ul aria-label="Import warnings">{warnings.map((w, i) => <li key={i} role="status">{w}</li>)}</ul>}
    </div>
  )
}

// The Field Engine: the instructions that vary the packet per send, and the global parameters.
export function FieldEngine({ doc, result, defs, onApply, rejected = null, error = null, readOnly = false }: FieldEngineProps) {
  const [adding, setAdding] = useState('')
  const [notice, setNotice] = useState<{ kind: 'status' | 'error'; text: string } | null>(null)
  const [importing, setImporting] = useState(false)
  const addList = useId()
  const feError = result?.field_engine?.error ?? null
  const failure = error !== null && (error.kind === 'request' || error.kind === 'restart') ? error.message : null

  // The editor judges the edit (and takes it back when the FE refuses it): the pane only says which parameter it was.
  function edit(key: string, value: string, make: (d: PacketDoc) => PacketDoc, label: string) {
    setNotice(null)
    onApply(make, label, { key, value })
  }
  const plain = (make: (d: PacketDoc) => PacketDoc, label: string) => { setNotice(null); onApply(make, label) }

  async function copy() {
    setNotice(null)
    if (!navigator.clipboard) { setNotice({ kind: 'error', text: 'The clipboard is not available here: a browser allows it only over https or on localhost.' }); return }
    try {
      await navigator.clipboard.writeText((await vmPython(doc)).code)
      setNotice({ kind: 'status', text: 'Copied' })
    } catch (e) { setNotice({ kind: 'error', text: e instanceof Error ? e.message : String(e) }) }
  }

  const known = defs.feInstructions.map((m) => m.id)
  const add = () => {
    if (!known.includes(adding)) { setNotice({ kind: 'error', text: `${adding === '' ? 'Choose' : `"${adding}" is not`} a Field Engine instruction` }); return }
    plain((d) => addInstruction(d, adding, defs), `add ${adding}`); setAdding('')
  }
  const metaOf = (id: string) => defs.feInstructionParameters.find((p) => p.id === id)
  const nameOf = (x: FeInstruction) => feText(x.parameters.name ?? x.parameters.fv_name)

  return (
    <div className="field-engine">
      {feError !== null && <p className="field-error" role="alert"><span className="mark-error" aria-hidden="true">!</span> {feError}</p>}
      {failure !== null && <p className="field-error" role="alert"><span className="mark-error" aria-hidden="true">!</span> {failure}</p>}
      <div className="row">
        <input type="text" aria-label="Instruction to add" list={addList} value={adding} placeholder="Add instruction" disabled={readOnly} spellCheck={false}
          onChange={(e) => setAdding(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add() } }} />
        <datalist id={addList}>{known.map((k) => <option key={k} value={k} />)}</datalist>
        <button type="button" disabled={readOnly} onClick={add}>Add</button>
        <button type="button" disabled={readOnly} onClick={() => setImporting((v) => !v)}>Import raw VM…</button>
        <button type="button" disabled={doc.feInstructions.length === 0} onClick={() => void copy()}>Copy instructions as Python</button>
      </div>
      {notice && <p role={notice.kind === 'error' ? 'alert' : 'status'} className={notice.kind === 'error' ? 'field-error' : 'muted'}>{notice.text}</p>}
      {importing && <ImportRaw result={result} onClose={() => setImporting(false)}
        onImport={(r) => plain((d) => setFe(d, { feInstructions: r.instructions, fePrarameters: r.globals }), 'import raw VM')} />}

      <h3>Structure</h3>
      {doc.feInstructions.length === 0
        ? <p className="muted">No Field Engine instructions.</p>
        : <ol aria-label="Field Engine structure" className="fe-tree">{doc.feInstructions.map((x, i) => <li key={i}>{x.id}{nameOf(x) ? ` (${nameOf(x)})` : ''}</li>)}</ol>}

      <h3>Global parameters</h3>
      {defs.feParameters.map((m) => (
        <div key={m.id} className="fe-global">
          <span className="fe-label">{m.name}</span>
          <ParamInput meta={m} label={m.name} value={feText(doc.fePrarameters[m.id])} readOnly={readOnly} doc={doc}
            rejected={rejected?.key === globalKey(m.id) ? rejected : null}
            onCommit={(v) => edit(globalKey(m.id), v, (d) => setGlobal(d, m.id, v), `set ${m.id}`)} />
        </div>
      ))}

      {doc.feInstructions.map((x, i) => {
        const meta = defs.feInstructions.find((m) => m.id === x.id)
        return (
          <section key={i} className="fe-instruction" data-instruction={i}>
            <div className="layer-head">
              <h3>{x.id}</h3>
              <button type="button" disabled={readOnly} aria-label={`Delete ${x.id} ${i + 1}`} onClick={() => plain((d) => deleteInstruction(d, i), `delete ${x.id}`)}>Delete</button>
            </div>
            {meta && meta.help && <details><summary>Help</summary><pre className="fe-help">{helpText(meta.help)}</pre></details>}
            {(meta?.parameters ?? Object.keys(x.parameters)).map((p) => {
              const pm = metaOf(p) ?? { id: p, name: p, type: 'STRING' as const }
              return (
                <div key={p} className="fe-row">
                  <span className="fe-label">{pm.name}</span>
                  <ParamInput meta={pm} label={`${pm.name} (${x.id} ${i + 1})`} value={feText(x.parameters[p])} readOnly={readOnly} doc={doc}
                    rejected={rejected?.key === paramKey(i, p) ? rejected : null}
                    onCommit={(v) => edit(paramKey(i, p), v, (d) => setParam(d, i, p, v), `set ${p}`)} />
                </div>
              )
            })}
          </section>
        )
      })}
    </div>
  )
}
