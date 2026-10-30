import { useEffect, useMemo, useState } from 'react'
import { RpcConsole } from '../components/RpcConsole'
import { formatEntry, LogView } from '../components/LogView'
import { downloadBlob } from '../download'
import { useChannelLog } from '../eventsChannel'
import { useAppState, useLogActions, type LogEntry } from '../state'
import { Tabs, tabPanel } from '../ui/Tabs'

type Kind = Exclude<LogEntry['kind'], 'MARK'>
type Tab = 'events' | 'rpc'
const CHIPS: { id: Kind; label: string }[] = [
  { id: 'ERROR', label: 'Errors' }, { id: 'WARNING', label: 'Warnings' }, { id: 'SERVER_EVENT', label: 'Server events' },
  { id: 'INFO', label: 'Info' }, { id: 'PING', label: 'Ping' },
]

// `trex-web-gui-events-YYYYMMDD-HHMMSSZ.txt`, from the UTC clock.
const fileName = (d: Date) => `trex-web-gui-events-${d.toISOString().replace(/\.\d+Z$/, 'Z').replace(/[-:]/g, '').replace('T', '-')}.txt`

// The log (filter, Copy, Save, Pop out, Clear) and the RPC console. In pop-out mode it renders a read-only log
// received over the BroadcastChannel and uses nothing that needs the AppStateProvider or the router.
export function EventsPage({ popout = false }: { popout?: boolean }) {
  return popout ? <PopoutEvents /> : <MainEvents />
}

// The pop-out window: only the log, from the main window.
function PopoutEvents() {
  const entries = useChannelLog()
  return (
    <section>
      <h1>Events</h1>
      {entries === null ? <p className="muted">Waiting for the main window</p> : <LogPanel entries={entries} popout />}
    </section>
  )
}

// The main window's page: Events and RPC tabs over the app state.
function MainEvents() {
  const { log, rpc, session } = useAppState()
  const { clear, seen } = useLogActions()
  const [tab, setTab] = useState<Tab>('events')
  // Everything on the page is read once it is shown, and again for each new entry while it is mounted.
  useEffect(() => { seen() }, [log, seen])
  return (
    <section>
      <h1>Events</h1>
      <Tabs label="Events views" idPrefix="events" tabs={[{ id: 'events', label: 'Events' }, { id: 'rpc', label: 'RPC' }]} selected={tab} onSelect={setTab} />
      <div {...tabPanel('events', 'events', tab)}><LogPanel entries={log} onClear={clear} /></div>
      <div {...tabPanel('events', 'rpc', tab)}><RpcConsole entries={rpc} on={session.rpc_console} includePolling={session.include_polling} /></div>
    </section>
  )
}

// The kind chips, text filter, actions and the table for one list of entries.
function LogPanel({ entries, popout = false, onClear }: { entries: LogEntry[]; popout?: boolean; onClear?: () => void }) {
  const [kinds, setKinds] = useState<Set<Kind>>(new Set())   // empty: All
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const e of entries) if (e.kind !== 'MARK') c[e.kind] = (c[e.kind] ?? 0) + 1
    return c
  }, [entries])
  const total = entries.filter((e) => e.kind !== 'MARK').length
  const needle = text.toLowerCase()
  // MARK rows are never filtered out.
  const shown = entries.filter((e) => e.kind === 'MARK' || ((kinds.size === 0 || kinds.has(e.kind)) && (!needle || e.message.toLowerCase().includes(needle))))
  const toggle = (k: Kind) => setKinds((s) => { const n = new Set(s); if (!n.delete(k)) n.add(k); return n })
  const joined = () => shown.map(formatEntry).join('\n')
  const copy = () => { setError(null); navigator.clipboard.writeText(joined()).catch((x: unknown) => setError(`Copy failed: ${x instanceof Error ? x.message : String(x)}`)) }
  return (
    <>
      <div className="events-bar">
        <button type="button" className="chip" aria-pressed={kinds.size === 0} onClick={() => setKinds(new Set())}>{`All ${total}`}</button>
        {CHIPS.map((c) => <button key={c.id} type="button" className="chip" aria-pressed={kinds.has(c.id)} onClick={() => toggle(c.id)}>{`${c.label} ${counts[c.id] ?? 0}`}</button>)}
        <input type="text" aria-label="Filter events" placeholder="Filter text" value={text} onChange={(e) => setText(e.target.value)} />
        <button type="button" onClick={copy} disabled={!shown.length}>Copy</button>
        <button type="button" onClick={() => downloadBlob(fileName(new Date()), new Blob([joined() + '\n'], { type: 'text/plain' }))} disabled={!shown.length}>Save log…</button>
        {!popout && typeof BroadcastChannel === 'function' && <button type="button" onClick={() => window.open('/events/popout', 'trex-web-gui-events', 'popup')}>Pop out ↗</button>}
        {!popout && onClear && <><span className="divider" role="separator" /><button type="button" className="danger" onClick={onClear}>Clear</button></>}
      </div>
      {error && <p role="alert" className="error">{error}</p>}
      <LogView entries={shown} summary={`${shown.filter((e) => e.kind !== 'MARK').length} entries · kept across reconnects`} />
    </>
  )
}
