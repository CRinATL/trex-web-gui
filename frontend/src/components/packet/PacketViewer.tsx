import { useEffect, useMemo, useState } from 'react'
import { packetTree, type DecodedRow, type PacketTree as Tree } from '../../api'
import { useHotkey } from '../../keys'
import { useDialogFocus } from '../useDialogFocus'
import { HexView } from './HexView'
import { decodeBase64, toHex, type Range } from './hexModel'
import { LayerList } from './LayerList'
import { fieldAt, layersOfTree } from './packetModel'

// The layers of one packet beside its bytes, read-only. The tree comes from the service (`packetTree`, which evaluates
// nothing); this viewer never fetches definitions or calls a packet route. If the tree fails the bytes still show.
export function PacketViewer({ row, onClose }: { row: DecodedRow; onClose: () => void }) {
  const ref = useDialogFocus<HTMLDivElement>('[data-autofocus]')
  const bytes = useMemo(() => decodeBase64(row.binary), [row.binary])
  // Results and the selection are tagged with the frame they belong to, so a row change shows nothing stale.
  const [result, setResult] = useState<{ binary: string; tree: Tree | null; error: string | null } | null>(null)
  const [pick, setPick] = useState<{ binary: string; range: Range } | null>(null)
  const mine = result?.binary === row.binary ? result : null
  const tree = mine?.tree ?? null
  const layers = useMemo(() => (tree ? layersOfTree(tree) : []), [tree])
  const [open, setOpen] = useState<{ binary: string; panes: ReadonlySet<number> } | null>(null)   // null: every layer open
  const panes = open?.binary === row.binary ? open.panes : new Set(layers.map((l) => l.index))
  const toggle = (i: number) => setOpen({ binary: row.binary, panes: (() => { const n = new Set(panes); if (!n.delete(i)) n.add(i); return n })() })
  const error = mine?.error ?? null
  const selected = pick?.binary === row.binary ? pick.range : null
  const setSelected = (range: Range) => setPick({ binary: row.binary, range })
  const [copied, setCopied] = useState<string | null>(null)
  useHotkey('escape', onClose)

  useEffect(() => {
    let live = true
    packetTree(row.binary).then(
      (t) => { if (live) setResult({ binary: row.binary, tree: t, error: null }) },
      (e: unknown) => { if (live) setResult({ binary: row.binary, tree: null, error: e instanceof Error ? e.message : String(e) }) })
    return () => { live = false }
  }, [row.binary])

  // A byte selects the narrowest field that holds it; outside every field it selects just itself.
  const selectByte = (i: number) => setSelected(fieldAt(layers, i)?.range ?? [i, i + 1])

  async function copy() {
    try { await navigator.clipboard.writeText(toHex(bytes)); setCopied('Copied') }
    catch (e) { setCopied(`Copy failed: ${e instanceof Error ? e.message : String(e)}`) }
  }

  return (
    <div className="modal packet-viewer" role="dialog" aria-modal="true" aria-labelledby="packet-title" ref={ref}>
      <h2 id="packet-title">Packet {row.index}</h2>
      <p className="packet-summary">{row.src !== '' || row.dst !== '' ? `${row.src} > ${row.dst} ` : ''}{row.proto} {row.wirelen} bytes{row.info ? `: ${row.info}` : ''}</p>
      <div className="packet-viewer-body">
        <div className="packet-viewer-tree">
          {error && <p role="alert" className="error">Packet tree unavailable: {error}</p>}
          {!mine && <p className="muted">Loading the packet tree...</p>}
          {tree && <LayerList layers={layers} expanded={panes} onToggle={toggle} onMove={() => undefined} onDelete={() => undefined} readOnly selected={selected} onSelectRange={setSelected} />}
        </div>
        <div className="packet-viewer-bytes"><HexView bytes={bytes} selected={selected} onSelectByte={selectByte} height={240} /></div>
      </div>
      <div className="dialog-footer">
        <button type="button" onClick={() => void copy()}>Copy hex</button>
        {copied && <span role="status" className="muted">{copied}</span>}
        <div className="end"><button type="button" onClick={onClose} data-autofocus>Close</button></div>
      </div>
    </div>
  )
}
