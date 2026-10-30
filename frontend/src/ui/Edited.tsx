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
