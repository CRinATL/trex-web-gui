import type { ReactNode } from 'react'
import type { PortView } from '../../api'

// One form row of the surrounding .form-grid: the label in the label column, a checkbox per port the server lists in the
// field column. `value` is the chosen port ids; `note` (an ⓘ) sits at the end of the row.
export function PortPicker({ label, ports, value, onChange, disabled, note }: {
  label: string; ports: PortView[]; value: number[]; onChange: (v: number[]) => void; disabled?: boolean; note?: ReactNode
}) {
  const toggle = (id: number, on: boolean) => onChange(on ? [...value, id].sort((a, b) => a - b) : value.filter((x) => x !== id))
  return (
    <div className="form-row">
      <span className="row-label">{label}</span>
      <div role="group" aria-label={label} className="port-picker">
        {ports.length === 0 && <span className="muted">no ports</span>}
        {ports.map((p) => (
          <label key={p.id} className="check">
            <input type="checkbox" checked={value.includes(p.id)} disabled={disabled} onChange={(e) => toggle(p.id, e.target.checked)} /> Port {p.id}
          </label>
        ))}
        {note}
      </div>
    </div>
  )
}
