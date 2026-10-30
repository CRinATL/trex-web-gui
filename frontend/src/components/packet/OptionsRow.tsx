import type { CombinedField } from './packetModel'

// IP and TCP options: the service's readable form, not editable here.
export function OptionsRow({ field }: { field: CombinedField }) {
  return <span className="options-row"><span className="field-value">{field.shown === '' ? 'none' : field.shown}</span> <span className="muted">(read-only)</span></span>
}
