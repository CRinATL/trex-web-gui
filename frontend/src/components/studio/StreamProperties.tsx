import { useId, useState, type ReactNode } from 'react'
import type { StreamJson, StreamPatch } from '../../api'
import { EditedTag, unsavedText } from '../../ui/Edited'
import { Info } from '../../ui/Info'
import { parseRate, readingOf } from '../../rateText'
import { formErrors, nextChoices, patchFrom, sentFields, toForm, type ModeType, type RxKind, type StreamFormValues, type StreamName } from './streamForm'

const MODES: [ModeType, string][] = [['continuous', 'Continuous'], ['single_burst', 'Single burst'], ['multi_burst', 'Multi-burst']]
const RX: [RxKind, string][] = [['none', 'None'], ['stats', 'Flow stats'], ['latency', 'Latency']]
const DST_MAC: [string, string][] = [['0', 'From config file'], ['1', 'From packet'], ['2', 'By ARP']]
const yesNo = (b: boolean | undefined) => (b ? 'yes' : 'no')

// The fields the form shows for `f`, as the grid below lays them out.
function shownFields(f: StreamFormValues): (keyof StreamFormValues)[] {
  return [
    'mode', ...(f.mode === 'single_burst' ? ['totalPkts' as const] : []), ...(f.mode === 'multi_burst' ? ['pktsPerBurst', 'count', 'ibg'] as const : []),
    'rate', 'enabled', 'selfStart', 'startPaused', 'next', 'actionCount', 'isg', 'randomSeed', 'coreId', 'srcMacFromPacket', 'dstMac', 'dummy', 'rx',
    ...(f.rx !== 'tpg' && f.rx !== 'none' ? ['pgId' as const] : []), 'cacheOn', ...(f.cacheOn ? ['cacheSize' as const] : []),
  ]
}

// The selected stream's properties (A6.2, A6.3, A6.6) in one .form-grid and one action bar. Save sends only the fields
// that differ from the stored stream (`patchFrom`), and is disabled while any field is invalid or nothing changed;
// `disabled` is the editor's index-shift lock or an ended writer. The form follows the stored stream: another stream
// replaces it (Previous and Next ask "Discard changes?" first); a stored stream that changed underneath updates only the
// fields whose stored value changed, so other unsaved edits stay. After a Save lands, Save waits for the page read.
// The name is not here: it is renamed in the table (one place), and an unchanged name never blocks a Save.
export function StreamProperties({ stream, index, names, onSave, onPrev, onNext, disabled = false }: {
  stream: StreamJson; index: number; names: (StreamName | null)[]
  onSave: (patch: StreamPatch) => void | boolean | Promise<void | boolean>;   // false: the write failed, Save stays available
  onPrev?: () => void; onNext?: () => void; disabled?: boolean
}) {
  const id = useId()
  const stored = JSON.stringify(stream)
  const storedForm = toForm(stream)
  // `base` is the form of the stored stream the edits started from; a field differs from it when it is dirty.
  const [seen, setSeen] = useState({ index, stored, base: storedForm })
  const [f, setF] = useState<StreamFormValues>(storedForm)
  const [saving, setSaving] = useState(false)
  const [sent, setSent] = useState<{ stored: string; patch: string } | null>(null)   // a saved patch whose page re-read has not landed
  const [leaving, setLeaving] = useState<'prev' | 'next' | null>(null)               // the "Discard changes?" question
  if (seen.index !== index) {
    // another stream: its stored values (moving away from edits is asked first, by Previous and Next)
    setSeen({ index, stored, base: storedForm }); setF(storedForm); setLeaving(null); setSent(null)
  } else if (seen.stored !== stored) {
    // the stored stream changed underneath (e.g. a table toggle, or this form's own save): a field takes the new stored
    // value only if that value changed; the user's edits to other fields stay
    const was = seen.base
    setSeen({ index, stored, base: storedForm })
    setF((cur) => {
      const next = { ...cur }
      for (const k of Object.keys(storedForm) as (keyof StreamFormValues)[]) if (storedForm[k] !== was[k]) (next as Record<string, unknown>)[k] = storedForm[k]
      return next
    })
  }

  const set = <K extends keyof StreamFormValues>(k: K, v: StreamFormValues[K]) => setF((cur) => ({ ...cur, [k]: v }))
  const errors = formErrors(f, names, stream.name, storedForm.next, storedForm.rate)
  const patch = patchFrom(stream, f)
  const patchText = JSON.stringify(patch)
  const empty = patchText === '{}'
  const awaiting = sent !== null && sent.stored === stored && sent.patch === patchText    // saved, not yet re-read: no resend
  const locked = disabled || saving
  const choices = nextChoices(names, stream.name)
  const parsedRate = parseRate(f.rate, 'pps')
  const rateReading = 'error' in parsedRate ? null : readingOf(parsedRate)
  // A field is marked edited (RC1-31) exactly when Save would send it, or when it holds an error and differs from the
  // stored text (R6): a field re-typed to an equal value is not. The count beside Save is of the shown fields so marked.
  type Key = keyof StreamFormValues & string
  const toSend = sentFields(stream, f)
  const isEdited = (k: Key) => toSend.has(k) || (errors[k] !== undefined && f[k] !== storedForm[k])
  const unsaved = unsavedText(shownFields(f).filter(isEdited).length)
  const cls = (k: Key, base?: string) => [base, isEdited(k) && 'edited'].filter(Boolean).join(' ') || undefined
  // a field's error and its edited tag are shown beside it and named by aria-describedby, the error first
  const describedBy = (k: Key) => [errors[k] && `${id}-${k}-err`, isEdited(k) && `${id}-${k}-ed`].filter(Boolean).join(' ') || undefined
  const bad = (k: Key) => ({ id: `${id}-${k}`, 'aria-invalid': errors[k] !== undefined, 'aria-describedby': describedBy(k) })
  const field = (k: Key, label: string, control: ReactNode, extra?: ReactNode) => {
    const ed = isEdited(k)
    return (
      <div className="form-row">
        <label htmlFor={`${id}-${k}`}>{label}</label>
        <span>{control}{ed && <EditedTag id={`${id}-${k}-ed`} />}{extra}{errors[k] && <span id={`${id}-${k}-err`} className="error"> {errors[k]}</span>}</span>
      </div>
    )
  }
  const text = (k: Key, label: string, extra?: ReactNode, off = false) =>
    field(k, label, <input {...bad(k)} className={cls(k, 'narrow')} value={f[k] as string} disabled={locked || off} onChange={(e) => set(k, e.target.value as never)} />, extra)
  const check = (k: Key, label: string, extra?: ReactNode) =>
    field(k, label, <input id={`${id}-${k}`} type="checkbox" className={cls(k)} aria-describedby={describedBy(k)} checked={f[k] as boolean} disabled={locked} onChange={(e) => set(k, e.target.checked as never)} />, extra)
  const pick = <T extends string>(k: Key, label: string, options: [T, string][], off = false) =>
    field(k, label, <select {...bad(k)} className={cls(k)} value={f[k] as string} disabled={locked || off} onChange={(e) => set(k, e.target.value as never)}>
      {options.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select>)

  async function submit() {
    setSaving(true)
    try {
      if ((await onSave(patch)) !== false) setSent({ stored, patch: patchText })
    } finally { setSaving(false) }
  }
  // Moving to another stream drops unsaved edits, so a dirty form asks first.
  function go(dir: 'prev' | 'next') {
    if (empty) (dir === 'prev' ? onPrev : onNext)?.()
    else setLeaving(dir)
  }
  const tpg = f.rx === 'tpg'
  const fs = stream.flow_stats

  return (
    <div className="stream-properties">
      <div className="form-grid">
        {field('mode', 'Mode', <select {...bad('mode')} className={cls('mode')} value={f.mode} disabled={locked}
          onChange={(e) => setF((cur) => ({ ...cur, mode: e.target.value as ModeType, next: e.target.value === 'continuous' ? '' : cur.next }))}>
          {MODES.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select>)}
        {f.mode === 'single_burst' && text('totalPkts', 'Packets')}
        {f.mode === 'multi_burst' && <>{text('pktsPerBurst', 'Packets per burst')}{text('count', 'Bursts')}{text('ibg', 'IBG (µs)')}</>}
        {text('rate', 'Rate', rateReading && <span className="muted"> {rateReading}</span>)}
        {check('enabled', 'Enabled')}
        {check('selfStart', 'Self start')}
        {check('startPaused', 'Start paused', <Info label="Start paused">Starts the stream in a paused state until it is resumed. Experimental in TRex.</Info>)}
        {field('next', 'Next', <select {...bad('next')} className={cls('next')} value={f.next} disabled={locked || f.mode === 'continuous'} onChange={(e) => set('next', e.target.value)}>
          <option value="">Stop</option>
          {choices.map((n) => <option key={JSON.stringify(n)} value={JSON.stringify(n)}>{String(n)}</option>)}
          {f.next !== '' && !choices.some((n) => JSON.stringify(n) === f.next) && <option value={f.next}>{String(JSON.parse(f.next))}</option>}
        </select>)}
        {text('actionCount', 'Action count')}
        {text('isg', 'ISG (µs)')}
        {text('randomSeed', 'Random seed', <span className="muted"> 0 = none</span>)}
        {text('coreId', 'Core', <span className="muted"> -1 = any</span>)}
        {check('srcMacFromPacket', 'Source MAC from packet', <Info label="the MAC flags">
          The <code>flags</code> field: bit 0 takes the source MAC from the packet, bits 1-2 choose the destination MAC (0 config file, 1 packet, 2 ARP), bit 3 marks a dummy stream, which is not sent but keeps its place in the chain.
        </Info>)}
        {pick('dstMac', 'Destination MAC', DST_MAC)}
        {check('dummy', 'Dummy stream')}
        {pick('rx', 'RX stats', tpg ? [...RX, ['tpg', 'TPG (edited in a later version)']] : RX, tpg)}
        {!tpg && f.rx !== 'none' && text('pgId', 'PG ID')}
        {tpg && fs.stream_id !== undefined && <div className="form-row"><span className="row-label">PG ID</span><span>{fs.stream_id}</span></div>}
        <div className="form-row">
          <span className="row-label">Extra stats</span>
          <span>
            <span>VXLAN: {yesNo(fs.vxlan)} (as imported)</span>{' '}<span>IEEE 1588: {yesNo(fs.ieee_1588)} (as imported)</span>{' '}
            <Info label="VXLAN and IEEE 1588">The TRex client drops both at import and at assign, so they never reach the server. They are shown as imported and cannot be edited here.</Info>
          </span>
        </div>
        {field('cacheOn', 'Cache', <select {...bad('cacheOn')} className={cls('cacheOn')} value={f.cacheOn ? 'on' : 'off'} disabled={locked} onChange={(e) => set('cacheOn', e.target.value === 'on')}>
          <option value="off">Off</option><option value="on">N packets</option></select>)}
        {f.cacheOn && text('cacheSize', 'Cache packets')}
      </div>
      {leaving && (
        <p role="group" aria-label="Discard changes">Discard changes?{' '}
          <button type="button" onClick={() => { const move = leaving === 'prev' ? onPrev : onNext; setLeaving(null); move?.() }}>Discard</button>{' '}
          <button type="button" onClick={() => setLeaving(null)}>Keep editing</button></p>
      )}
      <div className="action-bar">
        <button type="button" aria-label="Previous stream" disabled={!onPrev} onClick={() => go('prev')}>Previous</button>
        <button type="button" aria-label="Next stream" disabled={!onNext} onClick={() => go('next')}>Next</button>
        <button type="button" disabled={locked || empty} onClick={() => setF(toForm(stream))}>Cancel</button>
        <button type="button" disabled={locked || empty || awaiting || Object.keys(errors).length > 0} onClick={() => void submit()}>Save</button>
        {unsaved && <span className="unsaved-count">{unsaved}</span>}
      </div>
    </div>
  )
}
