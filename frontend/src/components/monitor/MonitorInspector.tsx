import { useMemo, useState, type ReactNode } from 'react'
import type { MonitorMsg } from '../../api'
import { useMonitor } from '../../monitorStore'
import { Tabs, tabPanel } from '../../ui/Tabs'
import { KEY_HINTS } from './inspectorHints'

export const MAX_NODES = 2000   // nodes rendered in one tree
const OPEN_DEPTH = 2            // levels shown open; deeper ones are folded
const KNOWN: { name: string; data: (m: MonitorMsg) => unknown }[] = [
  { name: 'trex-global', data: (m) => m.global }, { name: 'trex-latecny', data: (m) => m.latency }, { name: 'trex-latecny-v2', data: (m) => m.latency_v2 },
  { name: 'rx-check', data: (m) => m.rx_check }, { name: 'template_info', data: (m) => m.templates },
]

interface Entry { name: string; ts: number | null; data: unknown; text?: string; bytes?: number }

const isContainer = (v: unknown): v is object => typeof v === 'object' && v !== null
const childrenOf = (v: object): [string, unknown][] => (Array.isArray(v) ? v.map((x, i) => [String(i), x] as [string, unknown]) : Object.entries(v))

// A node's key matches, or something below it does. A matching key keeps its whole subtree.
function prune(key: string, value: unknown, needle: string): unknown {
  if (key.toLowerCase().includes(needle) || !isContainer(value)) return key.toLowerCase().includes(needle) ? value : undefined
  const kept = childrenOf(value).flatMap(([k, v]) => { const p = prune(k, v, needle); return p === undefined ? [] : [[k, p] as [string, unknown]] })
  // An array keeps its element indices as keys (an object), so element 5 is still "5" after filtering.
  return kept.length === 0 ? undefined : Object.fromEntries(kept)
}

// How many nodes the tree under `v` has, without building them.
const count = (v: unknown): number => (isContainer(v) ? childrenOf(v).reduce((n, [, c]) => n + 1 + count(c), 0) : 0)

// A fraction below TINY prints as `≈ 0` (String() would give 5.731e-8); the exact value stays in the leaf's tooltip and in Copy.
const TINY = 1e-4
const leafText = (v: unknown): string => (typeof v === 'string' ? JSON.stringify(v)
  : typeof v === 'number' && v !== 0 && Math.abs(v) < TINY ? '≈ 0' : String(v))
const leafTitle = (v: unknown): string | undefined => (typeof v === 'number' && v !== 0 && Math.abs(v) < TINY ? String(v) : undefined)

// One rendered node: a leaf (text) or a container (children, with its own size for the summary).
interface TNode { key: string; text?: string; title?: string; kind?: 'array' | 'object'; size?: number; children?: TNode[] }

// The first MAX_NODES nodes of the tree under `value`, in pre-order, built once and without touching anything shared, so a render that
// runs twice (StrictMode) gives the same tree.
function capTree(value: object): TNode[] {
  let left = MAX_NODES
  const walk = (v: object): TNode[] => {
    const out: TNode[] = []
    for (const [k, c] of childrenOf(v)) {
      if (left <= 0) break
      left--
      out.push(isContainer(c) ? { key: k, kind: Array.isArray(c) ? 'array' : 'object', size: Object.keys(c).length, children: walk(c) } : { key: k, text: leafText(c), title: leafTitle(c) })
    }
    return out
  }
  return walk(value)
}

function Tree({ nodes, depth }: { nodes: TNode[]; depth: number }): ReactNode {
  return nodes.map((n) => n.children === undefined
    ? <div key={n.key} className="insp-leaf" title={n.title ?? (depth === 0 ? KEY_HINTS[n.key] : undefined)}><span className="insp-key">{n.key}</span>: {n.text}</div>
    : (
      <details key={n.key} open={depth < OPEN_DEPTH}>
        <summary title={depth === 0 ? KEY_HINTS[n.key] : undefined}><span className="insp-key">{n.key}</span> {n.kind === 'array' ? `[${n.size}]` : `{${n.size}}`}</summary>
        <div className="insp-children"><Tree nodes={n.children} depth={depth + 1} /></div>
      </details>
    ))
}

function age(e: Entry, now: number | null): string {
  if (e.ts === null) return 'never'
  return now === null ? 'seen' : `${Math.max(0, now - e.ts).toFixed(1)} s ago`
}

function EntryView({ entry }: { entry: Entry }) {
  const [filter, setFilter] = useState('')
  const [copied, setCopied] = useState(false)
  const needle = filter.trim().toLowerCase()
  // The capped tree and the node total, computed once per entry and filter.
  const view = useMemo(() => {
    if (entry.text !== undefined || entry.data === null || entry.data === undefined) return undefined
    const root: unknown = isContainer(entry.data) ? entry.data : { value: entry.data }
    const shown = (needle === '' ? root : (prune('', root, needle) ?? {})) as object
    return { nodes: capTree(shown), total: count(shown) }
  }, [entry, needle])
  const copy = () => {
    const text = entry.text ?? JSON.stringify(entry.data, null, 2)
    void navigator.clipboard?.writeText(text).then(() => setCopied(true), () => setCopied(false))
  }
  return (
    <div>
      <div className="insp-bar">
        <label>Filter <input type="search" aria-label="Filter" value={filter} placeholder="key" onChange={(e) => setFilter(e.target.value)} /></label>
        <button type="button" onClick={copy}>Copy</button>
        {copied && <span role="status">Copied</span>}
      </div>
      {entry.text !== undefined ? (
        <>
          <p>truncated: {entry.bytes ?? '?'} bytes in all</p>
          <pre className="insp-text">{entry.text}</pre>
        </>
      ) : view === undefined ? <p>No data yet</p> : (
        <div className="insp-tree">
          {view.total === 0 && needle !== '' ? <p>No key matches</p> : <Tree nodes={view.nodes} depth={0} />}
          {view.total > MAX_NODES && <p>{view.total - MAX_NODES} more not shown</p>}
        </div>
      )}
    </div>
  )
}

// The Inspector: every name the publisher sends, the five known ones then each `other`, with how long ago it last arrived, and the
// latest data of the chosen one as a tree (a Filter on keys, a Copy button).
export function MonitorInspector() {
  const m = useMonitor((x) => x)
  const [selected, setSelected] = useState(0)
  const entries = useMemo<Entry[]>(() => {
    if (!m) return []
    const known = KNOWN.map((k): Entry => ({ name: k.name, ts: m.known_ts[k.name] ?? null, data: k.data(m) }))
    const other = Object.entries(m.other).map(([name, o]): Entry => (o.truncated || o.data_text !== undefined
      ? { name, ts: o.ts, data: null, text: o.data_text ?? '', bytes: o.bytes }
      : { name, ts: o.ts, data: o.data }))
    return [...known, ...other]
  }, [m])
  const at = Math.min(selected, entries.length - 1)
  if (entries.length === 0) return null
  return (
    <div className="monitor-inspector side-layout">
      <Tabs label="Published names" idPrefix="mon-insp" orientation="vertical" selected={String(at)} onSelect={(id) => setSelected(Number(id))}
        tabs={entries.map((e, i) => ({ id: String(i), label: <>{e.name} <small>{age(e, m?.ts ?? null)}</small></> }))} />
      {entries.map((e, i) => <div key={i} {...tabPanel('mon-insp', String(i), String(at))}>{i === at && <EntryView key={e.name} entry={e} />}</div>)}
    </div>
  )
}
