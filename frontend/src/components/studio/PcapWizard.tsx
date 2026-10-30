import { useState } from 'react'
import type { AddressRange, PcapOptions } from '../../api'
import { Info } from '../../ui/Info'
import { pcapError } from './pcapOptions'

const parse = (text: string) => (text.trim() === '' ? NaN : Number(text))
const shown = (v: number) => (Number.isFinite(v) ? String(v) : '')
const same = (a: number, b: number) => a === b || (Number.isNaN(a) && Number.isNaN(b))

// A number input that keeps what was typed ("0.", "") while it still means the value it was given.
function NumberField({ id, label, value, onValue, disabled, step = 'any' }: {
  id: string; label: string; value: number; onValue: (v: number) => void; disabled?: boolean; step?: string
}) {
  const [text, setText] = useState(shown(value))
  return <input id={id} aria-label={label} type="number" className="narrow" step={step} disabled={disabled}
    value={same(parse(text), value) ? text : shown(value)} onChange={(e) => { setText(e.target.value); onValue(parse(e.target.value)) }} />
}

// The pcap import options (legacy A5.9): name prefix, timing, loops and the two IPv4 rewrites. Fixed gap sends its
// gap with speedup 1; From the pcap sends ipg_usec null with the speedup. Its own error line names the first problem.
export function PcapWizard({ value, onChange }: { value: PcapOptions; onChange: (o: PcapOptions) => void }) {
  const set = (p: Partial<PcapOptions>) => onChange({ ...value, ...p })
  const fixed = value.ipg_usec !== null
  // what the other timing choice last held, so switching back and forth keeps both
  const [lastGap, setLastGap] = useState(NaN)
  const [lastSpeedup, setLastSpeedup] = useState(value.speedup)
  const problem = pcapError(value)
  const [starts, setStarts] = useState<Record<'rewrite_src' | 'rewrite_dst', string>>({ rewrite_src: value.rewrite_src?.start ?? '', rewrite_dst: value.rewrite_dst?.start ?? '' })

  function rewriteRow(key: 'rewrite_src' | 'rewrite_dst', what: string, short: string) {
    const rw: AddressRange | null = value[key]
    const setRw = (p: Partial<AddressRange>) => rw && set({ [key]: { ...rw, ...p } })
    // the backend's IPv4Address refuses surrounding spaces, so the value sent is trimmed; the field shows what was typed
    const [typed, setTyped] = [starts[key], (t: string) => setStarts((cur) => ({ ...cur, [key]: t }))]
    return (
      <div className="form-row">
        <label htmlFor={`pcap-${key}`}>Rewrite {what} IPv4</label>
        <span>
          <input id={`pcap-${key}`} type="checkbox" checked={rw !== null} onChange={(e) => set({ [key]: e.target.checked ? { start: '', count: 1 } : null })} />
          {rw && <>{' '}<input type="text" aria-label={`${short} start`} placeholder="16.0.0.1" value={typed.trim() === rw.start ? typed : rw.start} onChange={(e) => { setTyped(e.target.value); setRw({ start: e.target.value.trim() }) }} />
            {' '}<label htmlFor={`pcap-${key}-count`}>Count</label>{' '}
            <NumberField id={`pcap-${key}-count`} label={`${short} count`} step="1" value={rw.count} onValue={(count) => setRw({ count })} /></>}
        </span>
      </div>
    )
  }

  return (
    <fieldset className="pcap-wizard">
      <legend>Pcap options <Info label="pcap import">
        <p>Any pcap is accepted: each packet becomes one stream, chained in file order, and the last loops back to the first.</p>
        <p>The address rewrite needs IPv4 packets: an import that would rewrite a packet without IPv4 is refused, naming the packet.</p>
      </Info></legend>
      <div className="form-grid">
        <div className="form-row">
          <label htmlFor="pcap-prefix">Name prefix</label>
          <input id="pcap-prefix" type="text" value={value.name_prefix} onChange={(e) => set({ name_prefix: e.target.value })} />
        </div>
        <div className="form-row">
          <span className="row-label">Timing</span>
          <div role="radiogroup" aria-label="Timing" className="pcap-timing">
            <div className="pcap-timing-option">
              <label><input type="radio" name="pcap-timing" checked={!fixed}
                onChange={() => { setLastGap(value.ipg_usec ?? NaN); set({ ipg_usec: null, speedup: lastSpeedup }) }} /> From the pcap, sped up ×</label>
              <NumberField id="pcap-speedup" label="Speedup" value={value.speedup} disabled={fixed}
                onValue={(speedup) => { setLastSpeedup(speedup); set({ speedup }) }} />
            </div>
            <div className="pcap-timing-option">
              <label><input type="radio" name="pcap-timing" checked={fixed}
                onChange={() => { setLastSpeedup(value.speedup); set({ ipg_usec: lastGap, speedup: 1 }) }} /> Fixed gap</label>
              <NumberField id="pcap-gap" label="Gap (µs)" value={value.ipg_usec ?? lastGap} disabled={!fixed} onValue={(ipg_usec) => set({ ipg_usec })} />
            </div>
          </div>
        </div>
        <div className="form-row">
          <label htmlFor="pcap-loops">Loops</label>
          <NumberField id="pcap-loops" label="Loops" step="1" value={value.loop_count} onValue={(loop_count) => set({ loop_count })} />
        </div>
        {rewriteRow('rewrite_src', 'source', 'Source')}
        {rewriteRow('rewrite_dst', 'destination', 'Destination')}
      </div>
      {problem && <p role="alert" className="error">{problem}</p>}
    </fieldset>
  )
}
