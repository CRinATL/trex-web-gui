import { Fragment, useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { buildPreset, listPresets, type PacketDoc, type PresetInfo } from '../../api'
import { Tabs, tabPanel } from '../../ui/Tabs'

// The four presets (B2.8, replacing Simple Mode): one form each. A form turns its text boxes into the parameters the backend
// reads (strict: whole numbers as numbers, everything else as text) and Presets asks the backend for the document.
type Params = Record<string, unknown>
type Run = (id: string, params: () => Params) => void

class FormError extends Error {}

const at = (o: unknown, ...path: string[]): unknown => path.reduce<unknown>((v, k) => (typeof v === 'object' && v !== null ? (v as Params)[k] : undefined), o)
const text = (o: unknown, ...path: string[]): string => { const v = at(o, ...path); return v === undefined || v === null ? '' : String(v) }
const whole = (value: string, label: string): number => {
  if (!/^\d+$/.test(value.trim())) throw new FormError(`${label} must be a whole number`)
  return Number(value.trim())
}

const MAC_MODES = [['trex_config', 'From the TRex configuration'], ['packet', 'From the packet'], ['inc', 'Step up'], ['dec', 'Step down']] as const
const FRAME_MODES = [['fixed', 'Fixed'], ['inc', 'Step up'], ['dec', 'Step down'], ['random', 'Random']] as const
const OPS = [['inc', 'Step up'], ['dec', 'Step down'], ['random', 'Random']] as const

// One labelled row of a form-grid: a text box, or a select when `options` is given.
function Row({ label, value, onChange, options, narrow = true }: {
  label: string; value: string; onChange: (v: string) => void; options?: readonly (readonly [string, string])[]; narrow?: boolean
}) {
  const id = useId()
  return (
    <div className="form-row">
      <label htmlFor={id}>{label}</label>
      {options
        ? <select id={id} value={value} onChange={(e) => onChange(e.target.value)}>{options.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select>
        : <input id={id} type="text" className={narrow ? 'narrow' : undefined} value={value} spellCheck={false} onChange={(e) => onChange(e.target.value)} />}
    </div>
  )
}

function Preset({ id, name, blurb, status, busy, onSubmit, children }: {
  id: string; name: string; blurb: string; status: string | null; busy: boolean; onSubmit: () => void; children: ReactNode
}) {
  const title = useId()
  return (
    <section aria-labelledby={title} data-preset={id}>
      <h3 id={title}>{name}</h3>
      <p className="muted">{blurb}</p>
      <form className="form-grid" onSubmit={(e) => { e.preventDefault(); onSubmit() }} noValidate>
        {children}
        <div className="form-row">
          <span className="row-label" />
          <span><button type="submit" disabled={busy}>Open in editor</button>
            {status !== null && <span className="field-error" role="alert"> <span className="mark-error" aria-hidden="true">!</span> {status}</span>}</span>
        </div>
      </form>
    </section>
  )
}

interface FormProps { info: PresetInfo; run: Run; busy: boolean; status: string | null }

// A MAC: where it comes from, and for a stepped one its first value, how many and how far apart.
type MacState = { mode: string; value: string; count: string; step: string }
const macState = (d: unknown): MacState => ({ mode: text(d, 'mode'), value: text(d, 'value'), count: text(d, 'count'), step: text(d, 'step') })
function macParams(m: MacState, label: string): Params {
  if (m.mode === 'trex_config') return { mode: m.mode }
  if (m.mode === 'packet') return { mode: m.mode, value: m.value.trim() }
  return { mode: m.mode, value: m.value.trim(), count: whole(m.count, `${label} count`), step: whole(m.step, `${label} step`) }
}
function MacRows({ label, m, onChange }: { label: string; m: MacState; onChange: (m: MacState) => void }) {
  const set = (k: keyof MacState) => (v: string) => onChange({ ...m, [k]: v })
  return <>
    <Row label={`${label} MAC`} value={m.mode} options={MAC_MODES} onChange={set('mode')} />
    {m.mode !== 'trex_config' && <Row label={`${label} MAC address`} value={m.value} onChange={set('value')} narrow={false} />}
    {(m.mode === 'inc' || m.mode === 'dec') && <>
      <Row label={`${label} MAC count`} value={m.count} onChange={set('count')} />
      <Row label={`${label} MAC step`} value={m.step} onChange={set('step')} />
    </>}
  </>
}

function FloodForm({ info, run, busy, status }: FormProps) {
  const d = info.defaults
  const [s, setS] = useState({ l4: text(d, 'l4'), src_ip: text(d, 'src_ip'), dst_ip: text(d, 'dst_ip'), sport: text(d, 'sport'), dport: text(d, 'dport'),
    payload: text(d, 'payload'), frame: text(d, 'frame', 'mode'), min: text(d, 'frame', 'min'), max: text(d, 'frame', 'max') })
  const [src, setSrc] = useState(macState(at(d, 'src_mac')))
  const [dst, setDst] = useState(macState(at(d, 'dst_mac')))
  const set = (k: keyof typeof s) => (v: string) => setS((p) => ({ ...p, [k]: v }))
  const fixed = s.frame === 'fixed'
  return (
    <Preset id={info.id} name={info.name} busy={busy} status={status}
      blurb="One UDP or TCP packet sent over and over; the MAC addresses and the frame size can step or vary."
      onSubmit={() => run(info.id, () => {
        const min = whole(s.min, fixed ? 'Frame size' : 'Smallest frame')
        return {
          l4: s.l4, src_mac: macParams(src, 'Source'), dst_mac: macParams(dst, 'Destination'), src_ip: s.src_ip.trim(), dst_ip: s.dst_ip.trim(),
          sport: whole(s.sport, 'Source port'), dport: whole(s.dport, 'Destination port'), payload: s.payload,
          frame: { mode: s.frame, min, max: fixed ? min : whole(s.max, 'Largest frame') },
        }
      })}>
      <Row label="Protocol" value={s.l4} options={[['udp', 'UDP'], ['tcp', 'TCP']]} onChange={set('l4')} />
      <MacRows label="Source" m={src} onChange={setSrc} />
      <MacRows label="Destination" m={dst} onChange={setDst} />
      <Row label="Source IP" value={s.src_ip} onChange={set('src_ip')} narrow={false} />
      <Row label="Destination IP" value={s.dst_ip} onChange={set('dst_ip')} narrow={false} />
      <Row label="Source port" value={s.sport} onChange={set('sport')} />
      <Row label="Destination port" value={s.dport} onChange={set('dport')} />
      <Row label="Frame size" value={s.frame} options={FRAME_MODES} onChange={set('frame')} />
      <Row label={fixed ? 'Frame bytes (with FCS)' : 'Smallest frame (with FCS)'} value={s.min} onChange={set('min')} />
      {!fixed && <Row label="Largest frame (with FCS)" value={s.max} onChange={set('max')} />}
      <Row label="Payload text" value={s.payload} onChange={set('payload')} narrow={false} />
    </Preset>
  )
}

// One swept address: first address, step, direction, and the extent by count or by last address.
type SweepState = { start: string; step: string; op: string; by: 'count' | 'end'; count: string; end: string }
const sweepState = (d: unknown): SweepState => ({ start: text(d, 'start'), step: text(d, 'step'), op: text(d, 'op'), by: at(d, 'end') !== undefined ? 'end' : 'count',
  count: text(d, 'count') || '254', end: text(d, 'end') || text(d, 'start') })
function sweepParams(w: SweepState, label: string): Params {
  const base = { start: w.start.trim(), step: whole(w.step, `${label} step`), op: w.op }
  return w.by === 'count' ? { ...base, count: whole(w.count, `${label} count`) } : { ...base, end: w.end.trim() }
}
function SweepRows({ label, w, onChange }: { label: string; w: SweepState; onChange: (w: SweepState) => void }) {
  const set = (k: keyof SweepState) => (v: string) => onChange({ ...w, [k]: v } as SweepState)
  return <>
    <Row label={`${label} first address`} value={w.start} onChange={set('start')} narrow={false} />
    <Row label={`${label} step`} value={w.step} onChange={set('step')} />
    <Row label={`${label} order`} value={w.op} options={OPS} onChange={set('op')} />
    <Row label={`${label} range by`} value={w.by} options={[['count', 'Count'], ['end', 'Last address']]} onChange={set('by')} />
    {w.by === 'count'
      ? <Row label={`${label} count`} value={w.count} onChange={set('count')} />
      : <Row label={`${label} last address`} value={w.end} onChange={set('end')} narrow={false} />}
  </>
}

function IpSweepForm({ info, run, busy, status }: FormProps) {
  const d = info.defaults
  const [which, setWhich] = useState(text(d, 'which'))
  const [checksum, setChecksum] = useState(text(d, 'checksum'))
  const [src, setSrc] = useState(sweepState(at(d, 'src')))
  const [dst, setDst] = useState(sweepState(at(d, 'dst')))
  return (
    <Preset id={info.id} name={info.name} busy={busy} status={status}
      blurb="Source, destination or both IPv4 addresses stepped through a range, one packet for each."
      onSubmit={() => run(info.id, () => ({
        which, checksum,
        ...(which !== 'dst' ? { src: sweepParams(src, 'Source') } : {}), ...(which !== 'src' ? { dst: sweepParams(dst, 'Destination') } : {}),
      }))}>
      <Row label="Sweep" value={which} options={[['src', 'Source'], ['dst', 'Destination'], ['both', 'Both']]} onChange={setWhich} />
      {which !== 'dst' && <SweepRows label="Source" w={src} onChange={setSrc} />}
      {which !== 'src' && <SweepRows label="Destination" w={dst} onChange={setDst} />}
      <Row label="Checksum" value={checksum} options={[['ipv4', 'IPv4 header (software)'], ['hw', 'IPv4 and UDP (hardware)']]} onChange={setChecksum} />
    </Preset>
  )
}

function FrameSweepForm({ info, run, busy, status }: FormProps) {
  const d = info.defaults
  const [s, setS] = useState({ min: text(d, 'min'), max: text(d, 'max'), step: text(d, 'step'), op: text(d, 'op') })
  const set = (k: keyof typeof s) => (v: string) => setS((p) => ({ ...p, [k]: v }))
  return (
    <Preset id={info.id} name={info.name} busy={busy} status={status}
      blurb="UDP packets whose frame size steps or varies between two sizes."
      onSubmit={() => run(info.id, () => ({ min: whole(s.min, 'Smallest frame'), max: whole(s.max, 'Largest frame'), step: whole(s.step, 'Step'), op: s.op }))}>
      <Row label="Smallest frame (with FCS)" value={s.min} onChange={set('min')} />
      <Row label="Largest frame (with FCS)" value={s.max} onChange={set('max')} />
      <Row label="Step" value={s.step} onChange={set('step')} />
      <Row label="Order" value={s.op} options={OPS} onChange={set('op')} />
    </Preset>
  )
}

function VlanForm({ info, run, busy, status }: FormProps) {
  const first = at(info.defaults, 'tags') as unknown[] | undefined
  const [tags, setTags] = useState<{ vlan: string; prio: string }[]>(
    [0, 1].map((i) => ({ vlan: text(first?.[i], 'vlan') || String(100 + i), prio: text(first?.[i], 'prio') || '0' })))
  const [count, setCount] = useState(String(Math.max(1, first?.length ?? 1)))
  const n = Number(count)
  const set = (i: number, k: 'vlan' | 'prio') => (v: string) => setTags((p) => p.map((t, j) => (j === i ? { ...t, [k]: v } : t)))
  return (
    <Preset id={info.id} name={info.name} busy={busy} status={status}
      blurb="A UDP packet behind one or two 802.1Q tags."
      onSubmit={() => run(info.id, () => ({ tags: tags.slice(0, n).map((t, i) => ({ vlan: whole(t.vlan, `VLAN ${i + 1}`), prio: whole(t.prio, `Priority ${i + 1}`) })) }))}>
      <Row label="Tags" value={count} options={[['1', 'One'], ['2', 'Two (stacked)']]} onChange={setCount} />
      {tags.slice(0, n).map((t, i) => <Fragment key={i}>
        <Row label={`VLAN ${i + 1}`} value={t.vlan} onChange={set(i, 'vlan')} />
        <Row label={`Priority ${i + 1}`} value={t.prio} onChange={set(i, 'prio')} />
      </Fragment>)}
    </Preset>
  )
}

const FORMS: Record<string, (p: FormProps) => ReactNode> = { flood: FloodForm, ip_sweep: IpSweepForm, frame_size_sweep: FrameSweepForm, vlan: VlanForm }

// The presets page. A preset's document goes to `onOpen` together with the stream's MAC-mode bits (null: leave them).
export function Presets({ onOpen }: { onOpen: (doc: PacketDoc, flags: number | null) => void }) {
  const [presets, setPresets] = useState<PresetInfo[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<{ id: string; text: string } | null>(null)
  const [selected, setSelected] = useState('')
  const latest = useRef(0)

  useEffect(() => {
    let live = true
    listPresets().then((r) => { if (live) setPresets(r.presets) }, (e: unknown) => { if (live) setLoadError(e instanceof Error ? e.message : String(e)) })
    return () => { live = false; latest.current += 1 }       // nothing may reach onOpen once the page is gone
  }, [])

  // Only the latest request opens a document: the buttons are off while one runs, and a reply that is not the latest is dropped anyway.
  const run: Run = (id, params) => {
    let body: Params
    try { body = params() } catch (e) { setFailure({ id, text: e instanceof Error ? e.message : String(e) }); return }
    const mine = ++latest.current
    setFailure(null)
    setBusy(true)
    buildPreset(id, body).then(
      (r) => { if (mine === latest.current) { setBusy(false); onOpen(r.doc, r.flags) } },
      (e: unknown) => { if (mine === latest.current) { setBusy(false); setFailure({ id, text: e instanceof Error ? e.message : String(e) }) } })
  }

  if (loadError !== null) return <p className="field-error" role="alert"><span className="mark-error" aria-hidden="true">!</span> The presets could not be loaded: {loadError}</p>
  if (presets === null) return <p className="muted" role="status">Loading presets…</p>
  // One tab per preset, so the dialog's Close stays in view; every form stays mounted (hidden) and keeps its edits.
  const shown = presets.filter((info) => FORMS[info.id])
  const current = shown.some((info) => info.id === selected) ? selected : (shown[0]?.id ?? '')
  return (
    <div className="presets">
      <Tabs label="Presets" idPrefix="presets" tabs={shown.map((info) => ({ id: info.id, label: info.name }))} selected={current} onSelect={setSelected} />
      {shown.map((info) => {
        const Form = FORMS[info.id]
        return <div key={info.id} {...tabPanel('presets', info.id, current)}><Form info={info} run={run} busy={busy} status={failure?.id === info.id ? failure.text : null} /></div>
      })}
    </div>
  )
}
