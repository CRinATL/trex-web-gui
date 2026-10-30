import type { CombinedLayer } from './packetModel'

// The layer stack as one line, `Ether > IP > UDP`, with the frame length (B2.1). A click asks for that layer to be expanded.
export function Breadcrumb({ layers, length, onSelect }: { layers: CombinedLayer[]; length: number; onSelect?: (index: number) => void }) {
  return (
    <nav className="breadcrumb" aria-label="Layers">
      {layers.map((l, i) => (
        <span key={i}>
          {i > 0 && <span className="muted"> &gt; </span>}
          <button type="button" className="breadcrumb-layer" onClick={() => onSelect?.(i)}>{l.id}</button>
        </span>
      ))}
      <span className="muted breadcrumb-length"> {length} bytes</span>
    </nav>
  )
}
