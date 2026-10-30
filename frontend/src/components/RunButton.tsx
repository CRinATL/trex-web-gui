import type { ButtonHTMLAttributes, ReactNode } from 'react'

// Start, Stop and Pause buttons (RC1-20). The hue says what a button does: go green, stop red, hold amber. A soft outline
// means it is available; the lit form is the state now. Lit, the button is disabled and reads its state (● Transmitting,
// ❚❚ Paused), so the state never rests on colour alone. Every label shares one grid cell, so the button keeps its widest
// label's width and its neighbours do not move when it lights.
export type RunKind = 'go' | 'stop' | 'hold'
const GLYPH: Record<RunKind, string> = { go: '●', stop: '■', hold: '❚❚' }

type Props = Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'type'> & {
  kind: RunKind
  fits?: string[]          // other lit labels this slot can show (Transmitting / Paused), to reserve their width
  children: ReactNode
} & (
  | { lit?: undefined; litLabel?: undefined }   // a button that never lights
  | { lit?: boolean; litLabel: string }         // `lit` needs the state's name, so lit is never colour alone
)

export function RunButton({ kind, lit = false, litLabel, fits = [], disabled, children, ...rest }: Props) {
  const on = lit && litLabel !== undefined
  if (litLabel === undefined) {
    return <button type="button" data-run={kind} disabled={disabled} {...rest}>{children}</button>
  }
  const litForm = (label: string, glyph = GLYPH[kind]) => <><span data-glyph aria-hidden="true">{glyph} </span>{label}</>
  // the hidden labels take the widest glyph, so a slot that lights as Transmitting or as Paused keeps one width
  const ghost = (label: string) => litForm(label, GLYPH.hold)
  const labels = [...new Set([litLabel, ...fits])]
  const ghosts = on ? [children, ...labels.map(ghost)] : labels.map(ghost)
  return (
    <button type="button" data-run={kind} data-on={on || undefined} disabled={disabled || on} {...rest}>
      <span className="run-label">
        <span>{on ? litForm(litLabel) : children}</span>
        {ghosts.map((g, i) => <span key={i} className="run-ghost" aria-hidden="true" style={{ visibility: 'hidden' }}>{g}</span>)}
      </span>
    </button>
  )
}
