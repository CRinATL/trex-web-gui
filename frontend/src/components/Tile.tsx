import { useId, type ReactNode } from 'react'

// One labelled reading. `red` adds the red border and the flag text (colour is never the only signal); `flag` is that text;
// `hint` is the tooltip.
export function Tile({ name, text, red, flag = 'high', hint, children }: { name: string; text: string; red?: boolean; flag?: string; hint?: string; children?: ReactNode }) {
  return (
    <div role="group" aria-label={name} title={hint} className={`tile${red ? ' tile-red' : ''}`}>
      <span className="tile-name">{name}</span>
      <span className="tile-value">{text}</span>
      {children}
      {red && <span className="tile-flag">{flag}</span>}
    </div>
  )
}

// A column: the heading above its tiles, so three groups fit one row.
export function TileGroup({ name, children }: { name: string; children: ReactNode }) {
  const id = useId()
  return (
    <div role="group" aria-labelledby={id} className="tile-group">
      <h3 id={id}>{name}</h3>
      <div className="tile-row">{children}</div>
    </div>
  )
}
