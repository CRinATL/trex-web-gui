import { useState } from 'react'
import { emuCounters, macText, type EmuCounterRow, type EmuNsKey, type EmuScope } from '../../api'
import { baselineKey, counterId, deltaOf, readBaselines, storeBaseline, takeBaseline, type EmuBaselines } from '../../emuBaseline'
import { nsLabel, nsId } from '../../emuFormat'
import { emuStore, useEmu } from '../../emuStore'
import { COUNTERS_POLL_MS, useEmuPoll } from '../../useEmuPoll'
import { Info } from '../../ui/Info'

// Server severity codes (spec §4.5): 0x12 INFO, 0x13 WARNING, 0x14 ERROR. The text always shows; the colour only adds to it.
const SEVERITY: Record<number, [string, string]> = { 0x12: ['INFO', 'sev-info'], 0x13: ['WARNING', 'sev-warn'], 0x14: ['ERROR', 'sev-error'] }
const NO_SEVERITY: [string, string] = ['—', 'sev-info']   // a counter the server's meta does not describe has no code
const SCOPES: [EmuScope, string][] = [['ctx', 'Global'], ['ns', 'Namespace'], ['client', 'Client']]

// "since HH:MM:SS" in the browser's local time.
const clock = (at: number) => new Date(at).toTimeString().slice(0, 8)

// The severity badge of a counter row: INFO, WARNING or ERROR; any other code, or none, is a neutral dash.
function Severity({ info }: { info: number | null }) {
  const [text, cls] = (info === null ? undefined : SEVERITY[info]) ?? NO_SEVERITY
  return <span className={`badge ${cls}`}>{text}</span>
}

// The counters of the chosen scope: Global, the selected namespace, or the selected client of it. Each is read every second.
// "Clear" stores the current values in this browser as a baseline for the scope and shows the change since then; it sends nothing to the server.
export function EmuCounters({ ns, mac }: { ns: EmuNsKey | null; mac: readonly number[] | null }) {
  const [scope, setScope] = useState<EmuScope>('ctx')
  const need = scope === 'ns' && !ns ? 'Select a namespace above to read its counters.' : scope === 'client' && (!ns || !mac) ? 'Select a client above (Select button on its row) to read its counters.' : null
  return (
    <section className="emu-counters">
      <div className="page-head">
        <h2>Counters</h2>
        <Info label="the EMU counters">
          <p>Each table is one component of the server. Zero counters are hidden unless you turn that off. Clear remembers the current values in this browser only and shows the change since; the server&apos;s counters are never reset.</p>
        </Info>
      </div>
      <div className="toolbar" role="group" aria-label="Counter scope">
        {SCOPES.map(([s, label]) => (
          <button key={s} type="button" aria-pressed={scope === s} onClick={() => setScope(s)}>{label}</button>
        ))}
      </div>
      {need ? <p className="muted">{need}</p> : <ScopeCounters scope={scope} ns={ns} mac={mac} />}
    </section>
  )
}

// One scope's tables; mounted only when the scope has the key it needs, so nothing is read for a missing selection.
function ScopeCounters({ scope, ns, mac }: { scope: EmuScope; ns: EmuNsKey | null; mac: readonly number[] | null }) {
  const key = baselineKey(scope, ns, mac)
  const { data, error } = useEmuPoll(
    () => emuCounters(scope, scope === 'ctx' ? {} : scope === 'ns' ? { ns: ns! } : { ns: ns!, mac: mac! }),
    COUNTERS_POLL_MS, [key])
  const epoch = useEmu(() => emuStore.epoch)
  const [local, setLocal] = useState<{ epoch: number; bases: EmuBaselines }>(() => ({ epoch, bases: readBaselines() }))
  // A connection established anew or a load or removal clears the stored baselines (emuStore); the copy held here is then stale, and
  // storage is read again once and kept (not on every render until the next Clear).
  let bases = local.bases
  if (local.epoch !== epoch) { bases = readBaselines(); setLocal({ epoch, bases }) }
  const [hideZeros, setHideZeros] = useState(true)
  const [totals, setTotals] = useState(false)
  const base = bases[key]
  const deltas = base !== undefined && !totals
  const shown = (t: string, r: EmuCounterRow) => (deltas ? deltaOf(r.value, base.values[counterId(t, r)]) : r.value)
  const what = scope === 'ctx' ? 'global' : scope === 'ns' ? nsLabel(ns!) : `${nsLabel(ns!)}, client ${macText(mac!)}`
  return (
    <div>
      <p className="muted">Counters: {what}{ns && scope !== 'ctx' ? ` (${nsId(ns)})` : ''}</p>
      <div className="toolbar">
        <label><input type="checkbox" checked={hideZeros} onChange={(e) => setHideZeros(e.target.checked)} /> Hide zeros</label>
        <button type="button" disabled={!data} onClick={() => { if (data) { setLocal({ epoch, bases: storeBaseline(key, takeBaseline(data.tables, Date.now())) }); setTotals(false) } }}>Clear</button>
        {base !== undefined && <label><input type="checkbox" checked={totals} onChange={(e) => setTotals(e.target.checked)} /> Show totals</label>}
        {deltas && <span className="muted">since {clock(base.at)}</span>}
      </div>
      {error && <p role="alert" className="error">Could not read the counters: {error.message}</p>}
      {data && Object.entries(data.tables).map(([t, rows]) => {
        const visible = rows.filter((r) => !(hideZeros && shown(t, r) === 0))
        if (visible.length === 0) return null
        return (
          <details key={t} open>
            <summary>{t}</summary>
            <table>
              <thead><tr><th>Counter</th><th>Value</th><th>Unit</th><th>Severity</th></tr></thead>
              <tbody>
                {visible.map((r) => {
                  const v = shown(t, r)
                  return (
                    <tr key={r.name}>
                      <td title={r.help}>{r.name}</td><td>{v === null ? '—' : v}</td><td>{r.unit}</td><td><Severity info={r.info} /></td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </details>
        )
      })}
      {data && data.unsupported.length > 0 && (
        <ul className="unsupported">
          {data.unsupported.map((p) => <li key={p} className="unsupported-row">{p} — no counters here (not loaded, or not supported by this server)</li>)}
        </ul>
      )}
    </div>
  )
}
