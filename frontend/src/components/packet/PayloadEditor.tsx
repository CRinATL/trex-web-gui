import { useId, useState, type KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import type { FieldValue } from '../../api'
import { EditedTag, unsavedText } from '../../ui/Edited'
import { useDialogFocus } from '../useDialogFocus'
import { DEFAULT_SEED, fromValue, MODES, PayloadError, SIZE_MAX, toValue, type PayloadInput, type PayloadMode, type SizeKind } from './payloadModes'

const FILE_MODES: PayloadMode[] = ['file', 'file_pattern']
const SIZED_MODES: PayloadMode[] = ['text_pattern', 'file_pattern', 'code_pattern', 'random_ascii', 'random_bytes']
const HINT: Partial<Record<PayloadMode, string>> = {
  text_pattern: 'The text is repeated, and cut, to the size.',
  file_pattern: 'The file is repeated, and cut, to the size.',
  code_pattern: 'Hex bytes, repeated to the size. Without a size the packet carries them once.',
  random_ascii: 'Printable characters; the same seed gives the same bytes.',
  random_bytes: 'Any byte value; the same seed gives the same bytes.',
}

// The dialog for a BYTES field: one of seven ways to give the bytes, and for the generated ones a size, either of the
// payload itself or of the whole packet. Save hands the value to `onSave`; Cancel and Escape leave the field alone.
export function PayloadEditor({ value, onSave, onCancel }: { value: FieldValue | undefined; onSave: (value: FieldValue) => void; onCancel: () => void }) {
  const ref = useDialogFocus<HTMLDivElement>('[data-autofocus]')
  const id = useId()
  const [start] = useState(() => fromValue(value))
  const [mode, setMode] = useState<PayloadMode>(start.mode)
  const [input, setInput] = useState<PayloadInput>(start.input)
  const [sizeText, setSizeText] = useState(start.input.size === null ? '' : String(start.input.size))
  const [seedText, setSeedText] = useState(String(start.input.seed))
  const [fileName, setFileName] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const set = (patch: Partial<PayloadInput>) => { setInput((i) => ({ ...i, ...patch })); setProblem(null) }

  async function pick(file: File | undefined) {
    if (!file) return
    if (file.size > SIZE_MAX) { setProblem(`${file.name} is ${file.size} bytes; a file may be at most ${SIZE_MAX} bytes (1 MiB)`); return }   // refused before it is read
    try {
      const bytes = new Uint8Array(await file.arrayBuffer())
      setFileName(file.name)
      set({ bytes })
    } catch { setProblem(`${file.name} could not be read`) }
  }
  function save() {
    // an empty box means "not given"; anything else must be a number
    const size = sizeText.trim() === '' ? null : Number(sizeText)
    const seed = seedText.trim() === '' ? NaN : Number(seedText)
    try { onSave(toValue(mode, { ...input, size: size !== null && Number.isNaN(size) ? 0 : size, seed })) }
    catch (x) { setProblem(x instanceof PayloadError ? x.message : String(x)) }
  }
  function onKeyDown(e: KeyboardEvent) {
    if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'o')) e.preventDefault()   // not the browser's Save Page or Open (the editor would have prevented them)
    e.stopPropagation()   // the editor's own keys (undo, redo, delete layer) must not reach it from a dialog opened inside it (the portal still bubbles in React)
    if (e.key === 'Escape') onCancel()
  }

  const sized = SIZED_MODES.includes(mode)
  // A control that differs from the value the dialog opened on is marked edited (RC1-31); the shown ones are counted beside Save.
  const textShown = mode === 'text' || mode === 'text_pattern' || mode === 'code_pattern'
  const seedShown = mode === 'random_ascii' || mode === 'random_bytes'
  const edited = {
    mode: mode !== start.mode,
    text: textShown && input.text !== start.input.text,
    file: FILE_MODES.includes(mode) && input.bytes !== start.input.bytes,
    kind: sized && input.sizeKind !== start.input.sizeKind,
    // as numbers, as save() reads them, so 09 over 9 is not an edit (R6); an empty Size is "not given"
    size: sized && (sizeText.trim() === '' ? null : Number(sizeText)) !== start.input.size,
    seed: seedShown && Number(seedText) !== start.input.seed,
  }
  type Part = keyof typeof edited
  const marks = (k: Part) => (edited[k] ? { 'aria-describedby': `${id}-${k}-ed` } : {})
  const cls = (k: Part, base?: string) => [base, edited[k] && 'edited'].filter(Boolean).join(' ') || undefined
  const tag = (k: Part) => edited[k] && <EditedTag id={`${id}-${k}-ed`} />
  const unsaved = unsavedText(Object.values(edited).filter(Boolean).length)
  const dialog = (
    <div role="dialog" aria-modal="true" aria-label="Payload" className="modal payload-dialog" ref={ref} onKeyDown={onKeyDown}
      onContextMenu={(e) => e.stopPropagation()}>   {/* a right-click is the browser's (Paste), not the field row's menu around the portal */}
      <h2>Payload</h2>
      <div className="payload-row">
        <label htmlFor={`${id}-mode`}>Source</label>
        <select id={`${id}-mode`} {...marks('mode')} className={cls('mode')} data-autofocus value={mode} onChange={(e) => { setMode(e.target.value as PayloadMode); setProblem(null) }}>
          {MODES.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
        </select>
        {tag('mode')}
      </div>
      {(mode === 'text' || mode === 'text_pattern') && (
        <div className="payload-row">
          <label htmlFor={`${id}-text`}>Text</label>
          <textarea id={`${id}-text`} {...marks('text')} className={cls('text', 'payload-text')} rows={5} spellCheck={false} value={input.text} onChange={(e) => set({ text: e.target.value })} />
          {tag('text')}
        </div>
      )}
      {mode === 'code_pattern' && (
        <div className="payload-row">
          <label htmlFor={`${id}-hex`}>Hex bytes</label>
          <textarea id={`${id}-hex`} {...marks('text')} className={cls('text', 'payload-text')} rows={3} spellCheck={false} placeholder="0a 1b 2c" value={input.text} onChange={(e) => set({ text: e.target.value })} />
          {tag('text')}
        </div>
      )}
      {FILE_MODES.includes(mode) && (
        <div className="payload-row">
          <label htmlFor={`${id}-file`}>File</label>
          <input id={`${id}-file`} {...marks('file')} className={cls('file')} type="file" onChange={(e) => { void pick(e.target.files?.[0]); e.target.value = '' }} />
          <span className="muted mono">{input.bytes !== null ? `${fileName ?? 'current content'}: ${input.bytes.length} bytes` : 'no file chosen'}</span>
          {tag('file')}
        </div>
      )}
      {sized && (
        <div className="payload-row">
          <label htmlFor={`${id}-kind`}>Size is</label>
          <select id={`${id}-kind`} {...marks('kind')} className={cls('kind')} value={input.sizeKind} onChange={(e) => set({ sizeKind: e.target.value as SizeKind })}>
            <option value="load">payload length</option>
            <option value="packet">packet size (with FCS)</option>
          </select>
          {tag('kind')}
          <label htmlFor={`${id}-size`}>Size</label>
          <input id={`${id}-size`} {...marks('size')} type="text" inputMode="numeric" className={cls('size', 'mono')} value={sizeText} onChange={(e) => { setSizeText(e.target.value); setProblem(null) }} />
          {tag('size')}
          <span className="muted">bytes, 1 to {SIZE_MAX}</span>
        </div>
      )}
      {(mode === 'random_ascii' || mode === 'random_bytes') && (
        <div className="payload-row">
          <label htmlFor={`${id}-seed`}>Seed</label>
          <input id={`${id}-seed`} {...marks('seed')} type="text" inputMode="numeric" className={cls('seed', 'mono')} value={seedText} onChange={(e) => { setSeedText(e.target.value); setProblem(null) }} />
          {tag('seed')}
          <span className="muted">not 0: the service reads 0 as {DEFAULT_SEED}</span>
        </div>
      )}
      {start.note !== undefined && <p role="note" className="muted">{start.note}</p>}
      {HINT[mode] && <p className="muted">{HINT[mode]}</p>}
      {sized && input.sizeKind === 'packet' && <p className="muted">The packet size is the whole frame with its 4-byte FCS; the headers and the FCS are taken off to get the payload.</p>}
      {problem !== null && <p role="alert" className="error">{problem}</p>}
      <div className="dialog-footer">
        <div className="end">
          <button type="button" onClick={onCancel}>Cancel</button>
          <button type="button" className="primary" onClick={save}>Save</button>
          {unsaved && <span className="unsaved-count">{unsaved}</span>}
        </div>
      </div>
    </div>
  )
  return createPortal(dialog, document.body)
}

