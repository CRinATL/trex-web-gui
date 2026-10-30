import { useEffect, useId, useState } from 'react'
import { payloadClasses, type Definitions, type PacketDoc } from '../../api'

// Add a layer at the end. The protocols the last layer can carry come first in the list (B2.4); every protocol follows.
export function AppendLayer({ doc, defs, onAppend }: { doc: PacketDoc; defs: Definitions; onAppend: (id: string) => void }) {
  const [text, setText] = useState('')
  const [classes, setClasses] = useState<string[]>([])
  const [problem, setProblem] = useState<string | null>(null)
  const listId = useId()
  const stack = doc.packet.map((l) => l.id).join('\n')

  useEffect(() => {
    let live = true
    payloadClasses(doc).then((r) => { if (live) setClasses(r.classes) }, () => { if (live) setClasses([]) })
    return () => { live = false }
  // the stack of layers decides the classes; a field commit or a Field Engine edit leaves it as it was, and the engine is serial, so it asks nothing then
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stack])

  const known = new Set(defs.protocols.map((p) => p.id))
  const first = [...new Set(classes.filter((c) => known.has(c)))]
  const options = [...first, ...defs.layerIds.filter((id) => !first.includes(id))]

  function add() {
    const t = text.trim()
    const id = options.find((o) => o === t) ?? options.find((o) => o.toLowerCase() === t.toLowerCase())
    if (id === undefined) { setProblem(t === '' ? 'enter a protocol' : `unknown protocol "${t}"`); return }
    setProblem(null)
    setText('')
    onAppend(id)
  }

  return (
    <div className="append-layer">
      <input type="text" aria-label="Add layer" list={listId} value={text} placeholder="Add layer…" spellCheck={false}
        onChange={(e) => { setText(e.target.value); setProblem(null) }}
        onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add() } }} />
      <datalist id={listId}>{options.map((o) => <option key={o} value={o} />)}</datalist>
      <button type="button" onClick={add}>Add</button>
      {problem !== null && <span className="field-error" role="alert"><span className="mark-error" aria-hidden="true">!</span> {problem}</span>}
    </div>
  )
}
