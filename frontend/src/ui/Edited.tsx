import { Popover } from './Info'

// The unsaved-edit marker (RC1-31): a control that differs from its stored value gets className="edited" (a dashed teal box,
// a pale teal fill) and this tag after it. `id` lets the control append the tag to its aria-describedby, after any error id.
export function EditedTag({ id }: { id?: string } = {}) {
  return <span id={id} className="edited-tag">edited</span>
}

// The count shown beside a form's Save: '' when nothing is edited.
// eslint-disable-next-line react-refresh/only-export-components -- the count text belongs with the tag it counts
export function unsavedText(n: number): string {
  return n <= 0 ? '' : `${n} unsaved change${n === 1 ? '' : 's'}`
}

// One edited field as the change list shows it: its label, the value Save would replace, and the value it would store.
export interface UnsavedChange { field: string; saved: string; edited: string }

const SHOWN_MAX = 80   // code points of a value the list shows; a longer one is cut, its whole text in a (mouse-only) title

// A value as the change list prints it: an empty one named, a long one cut to SHOWN_MAX code points (never inside a
// surrogate pair, as the forms count code points).
// eslint-disable-next-line react-refresh/only-export-components -- the forms format their values with it
export function shown(v: string): string {
  if (v === '') return '(empty)'
  const points = [...v]
  return points.length > SHOWN_MAX ? `${points.slice(0, SHOWN_MAX - 1).join('')}…` : v
}

// The count beside a form's Save as a button: it opens a floating list of the edited fields, each with its saved and
// edited value. Nothing is rendered when nothing is edited. The list closes as an Info note does (Escape, outside click).
export function UnsavedChanges({ changes }: { changes: UnsavedChange[] }) {
  if (changes.length === 0) return null
  const cell = (v: string) => <td title={v !== '' && shown(v) !== v ? v : undefined}>{shown(v)}</td>   // a cut value's whole text
  return (
    <Popover trigger={unsavedText(changes.length)} buttonClass="unsaved-count" panelClass="info-text unsaved-list">
      <table aria-label="Unsaved changes">
        <thead><tr><th scope="col">Field</th><th scope="col">Saved</th><th scope="col">Edited</th></tr></thead>
        <tbody>{changes.map((c) => <tr key={c.field}><th scope="row">{c.field}</th>{cell(c.saved)}{cell(c.edited)}</tr>)}</tbody>
      </table>
    </Popover>
  )
}
