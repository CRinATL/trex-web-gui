import { useRef, useState, type ReactNode } from 'react'
import { applyLayer, type LayerBody, type PortView } from '../api'
import { layerView, type LayerView } from '../layerView'
import type { Selection } from '../portActions'
import { errText, isIpv4, isIpv6, p3bBlock, sentence } from '../portGate'
import { useBusy } from '../state'

// The backend's refusal text for IPv6 on the legacy stack (LEGACY_IPV6 in backend/trex_web_gui/sessions/port_config.py); shown without an RPC.
const IPV6_LEGACY = 'IPv6 configuration needs the linux_based stack; this server runs legacy'
const MAC = /^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$/

type V6Mode = 'off' | 'auto' | 'address'
interface Form { mode: 'L2' | 'L3'; dstMac: string; srcIpv4: string; dstIpv4: string; vlanOuter: string; vlanInner: string; v6: V6Mode; v6Addr: string }

const formOf = (v: LayerView): Form => ({
  mode: v.mode, dstMac: v.dstMac ?? '', srcIpv4: v.srcIpv4 ?? '', dstIpv4: v.dstIpv4 ?? '',
  vlanOuter: v.vlan[0] === undefined ? '' : String(v.vlan[0]), vlanInner: v.vlan[1] === undefined ? '' : String(v.vlan[1]),
  v6: v.ipv6 === 'off' ? 'off' : v.ipv6 === 'auto' ? 'auto' : 'address', v6Addr: v.ipv6 === 'off' || v.ipv6 === 'auto' ? '' : v.ipv6,
})
const keyOf = (p: PortView) => JSON.stringify([p.layer_cfg, p.vlan])

// Returns the first problem per field, or the request body when the form is valid. A first check only (isIpv4 accepts
// leading-zero octets, isIpv6 accepts ":::"); the backend's 422 is authoritative and its text is shown as the error.
function build(port: number, f: Form, v6Allowed: boolean): { errors: string[]; body: LayerBody | null } {
  const errors: string[] = []
  const vlan: number[] = []
  for (const [label, raw] of [['outer', f.vlanOuter], ['inner', f.vlanInner]] as const) {
    const s = raw.trim()
    if (s === '') continue
    const n = Number(s)
    if (!/^\d+$/.test(s) || n < 1 || n > 4095) errors.push(`The ${label} VLAN must be a number from 1 to 4095.`)
    else vlan.push(n)
  }
  if (f.vlanOuter.trim() === '' && f.vlanInner.trim() !== '') errors.push('Enter the outer VLAN before the inner one.')
  const body: LayerBody = { port, mode: f.mode, vlan }
  if (f.mode === 'L2') {
    if (!MAC.test(f.dstMac.trim())) errors.push('The destination MAC must look like 00:11:22:33:44:55.')
    else body.dst_mac = f.dstMac.trim().toLowerCase()
  } else {
    const src = f.srcIpv4.trim(), dst = f.dstIpv4.trim()
    if (!isIpv4(src) || !isIpv4(dst)) errors.push('Source and destination must both be IPv4.')
    else { body.src_ipv4 = src; body.dst_ipv4 = dst }
  }
  if (v6Allowed) {
    if (f.v6 === 'off') body.ipv6 = { enabled: false }
    else if (f.v6 === 'auto') body.ipv6 = { enabled: true }
    else if (!isIpv6(f.v6Addr.trim())) errors.push('The IPv6 source must be an IPv6 address.')
    else body.ipv6 = { enabled: true, src: f.v6Addr.trim() }
  }
  return { errors, body: errors.length ? null : body }
}

type Outcome = { kind: 'ok' | 'warn'; text: string } | null

export function PortConfigTab({ port, readOnly, connected = true, selection = { kind: 'port', id: port.id }, onResolved }: {
  port: PortView; readOnly: boolean; connected?: boolean; selection?: Selection; onResolved?: () => void
}) {
  const id = port.id
  const view = layerView(port)
  const legacy = port.info.stack === 'legacy'
  const busyKind = useBusy()[id]
  const [applying, setApplying] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [restoreError, setRestoreError] = useState<string | null>(null)
  const [outcome, setOutcome] = useState<Outcome>(null)
  const [tried, setTried] = useState(false)
  // why the last Apply left ARP unresolved, with the view it was about: it is dropped once the view's ARP state or destination moves on
  const [arpReason, setArpReason] = useState<{ sig: string; text: string } | null>(null)
  const arpSig = (v: LayerView) => JSON.stringify([v.mode, v.arp, v.dstIpv4])
  const inFlight = useRef(false)

  // The form is seeded from the server's view; `seed` is what it was seeded from, so a later change can be told from an edit.
  // `stale` is the prop key that was current when our own Apply succeeded: until the polled prop moves past it, it is not a server change.
  const [seed, setSeed] = useState<{ key: string; form: Form; stale?: string }>(() => ({ key: keyOf(port), form: formOf(view) }))
  const [form, setForm] = useState<Form>(seed.form)
  const dirty = JSON.stringify(form) !== JSON.stringify(seed.form)
  const serverKey = keyOf(port)
  const changed = serverKey !== seed.key && serverKey !== seed.stale
  const reseed = (p: PortView, stale?: string) => { const f = formOf(layerView(p)); setSeed({ key: keyOf(p), form: f, stale }); setForm(f); setTried(false) }
  if (seed.stale !== undefined && serverKey === seed.key) setSeed({ ...seed, stale: undefined })   // the prop caught up
  // adjusting state during render: an untouched form follows the server, an edited one stays and offers Reload
  if (changed && !dirty && !applying) reseed(port)

  const edit = (patch: Partial<Form>) => { setForm({ ...form, ...patch }); setTried(false) }

  const why = p3bBlock({ port, connected, readOnly, selection, busyKind })
  const disabled = applying || why !== null
  const v6Off = disabled || legacy

  const { errors, body } = build(id, form, !legacy)

  async function apply() {
    setTried(true)
    if (!body || inFlight.current) return
    inFlight.current = true; setApplying(true); setError(null); setRestoreError(null); setOutcome(null)
    try {
      const r = await applyLayer(body)
      const mine = r.ports.find((p) => p.id === id)
      if (mine) reseed(mine, serverKey)
      setArpReason(r.arp?.state === 'unresolved' ? { sig: arpSig(mine ? layerView(mine) : view), text: r.arp.error ?? 'no reason given' } : null)
      if (r.arp?.state === 'unresolved') setOutcome({ kind: 'warn', text: `L3 set, ARP unresolved: ${r.arp.error ?? 'no reason given'}` })
      else setOutcome({ kind: 'ok', text: 'Applied' })
      if (r.service_restore_error) setRestoreError(r.service_restore_error)
      onResolved?.()
    } catch (x) { setError(errText(x)) }
    finally { inFlight.current = false; setApplying(false) }
  }

  const title = why ?? undefined
  const f = (name: string) => `config-${name}-${id}`
  const row = (label: string, name: string, control: ReactNode) => (
    <div className="form-row"><label htmlFor={f(name)}>{label}</label>{control}</div>
  )
  // a value the user cannot edit: the label names the value through aria-labelledby, as it names an input through htmlFor
  const fixed = (label: string, name: string, value: ReactNode, note?: string) => (
    <div className="form-row"><label id={f(name)}>{label}</label><span><span aria-labelledby={f(name)}>{value}</span>{note && <>{' '}<span className="muted">{note}</span></>}</span></div>
  )
  const text = (label: string, name: keyof Form) => row(label, name,
    <input id={f(name)} type="text" value={form[name]} disabled={disabled} title={title} onChange={(e) => edit({ [name]: e.target.value })} />)
  const vlan = (label: string, name: 'vlanOuter' | 'vlanInner') => row(label, name,
    <input id={f(name)} type="text" inputMode="numeric" value={form[name]} disabled={disabled} title={title} onChange={(e) => edit({ [name]: e.target.value })} />)
  const l3 = form.mode === 'L3'
  return (
    <div className="config-tab">
      {changed && dirty && (
        <p role="status" className="error">Changed on the server — <button type="button" onClick={() => reseed(port)}>Reload</button></p>
      )}
      <section>
        <h3>Mode and VLAN</h3>
        <div className="form-grid">
          <div className="form-row">
            <label id={f('mode')}>Mode</label>
            <div role="radiogroup" aria-labelledby={f('mode')} className="toggles">
              {(['L2', 'L3'] as const).map((m) => (
                <label key={m} className="check"><input type="radio" name={`mode-${id}`} checked={form.mode === m} disabled={disabled} title={title} onChange={() => edit({ mode: m })} /> {m}</label>
              ))}
            </div>
          </div>
          {vlan('Outer VLAN', 'vlanOuter')}
          {vlan('Inner VLAN', 'vlanInner')}
          {fixed('Source MAC', 'srcMac', view.srcMac || '–', 'set in the server config')}
          {l3
            ? fixed('Destination MAC', 'dstMac', view.mode !== 'L3' ? 'resolved after Apply' : view.dstMac ?? (arpReason?.sig === arpSig(view) ? `unresolved: ${arpReason.text}` : 'unresolved'))
            : text('Destination MAC', 'dstMac')}
        </div>
      </section>

      <section>
        <h3>Addresses</h3>
        <div className="form-grid">
          {l3 && text('Source IPv4', 'srcIpv4')}
          {l3 && row('Destination IPv4', 'dstIpv4', <span><input id={f('dstIpv4')} type="text" value={form.dstIpv4} disabled={disabled} title={title} onChange={(e) => edit({ dstIpv4: e.target.value })} />{' '}
            <span className="muted">{view.arp ?? '–'}</span></span>)}
          <div className="form-row">
            <label id={f('v6')}>IPv6</label>
            <div role="radiogroup" aria-labelledby={f('v6')} className="toggles">
              {([['off', 'Off'], ['auto', 'Auto'], ['address', 'Address']] as const).map(([m, label]) => (
                <label key={m} className="check"><input type="radio" name={`ipv6-${id}`} checked={form.v6 === m} disabled={v6Off} title={legacy ? IPV6_LEGACY : title} onChange={() => edit({ v6: m })} /> {label}</label>
              ))}
            </div>
          </div>
          {row('IPv6 source', 'v6Addr', <input id={f('v6Addr')} type="text" value={form.v6Addr} disabled={v6Off || form.v6 !== 'address'} title={legacy ? IPV6_LEGACY : title} onChange={(e) => edit({ v6Addr: e.target.value })} />)}
        </div>
        {legacy && <p className="muted">{IPV6_LEGACY}</p>}
      </section>

      <div className="action-bar">
        <button type="button" disabled={disabled} title={title} onClick={() => void apply()}>Apply</button>
        <button type="button" disabled={!dirty || applying} onClick={() => reseed(port)}>Revert</button>
        {dirty && <span className="muted">Unapplied changes</span>}
        {why && <span className="muted">{sentence(why)}</span>}
      </div>
      {tried && errors.map((e) => <p key={e} role="alert" className="error">{e}</p>)}
      {error && <p role="alert" className="error">{error}</p>}
      {restoreError && <p role="alert" className="error">Service mode could not be restored: {restoreError}</p>}
      {outcome && <p role="status" className={outcome.kind === 'warn' ? 'error' : undefined}>{outcome.text}</p>}
    </div>
  )
}
