import { useEffect, useRef, useState } from 'react'
import { ApiError, putPortLabel, type PortView } from '../api'
import { useAppState } from '../state'
import { cpLength, DESCRIPTION_MAX, NAME_MAX, oneLine, pasteInto, portKey, usePortLabelOf, type PortLabel } from '../portLabels'
import { confirmedName } from './ifaceText'
import { EditedTag, unsavedText } from '../ui/Edited'

interface FormProps {
  where: { server: string; key: string } | null   // null without a connection: nothing can be saved
  saved: PortLabel | null; suggestion: string | null; loadError: string | null
  focus?: boolean; onFocused?: () => void
}

// The inputs and their state. The caller keys it on the connection and the label key, so a new connect or another port
// starts it afresh. The fields start from the target; a later change to it replaces them only while they hold no typed edit.
function NameForm({ where, saved, suggestion, loadError, focus, onFocused }: FormProps) {
  // what Revert goes back to: the saved label, else the suggestion (a confirmed kernel name)
  const target = saved ?? { name: suggestion ?? '', description: '' }
  const [name, setName] = useState(target.name)
  const [description, setDescription] = useState(target.description)
  // Follow a label that changes underneath: another browser saves or deletes it, or the server trimmed what was saved.
  // Per field: a typed edit is kept; a field with none (or whose text is exactly the trimmed new value) adopts the new value,
  // even while the other field holds an edit.
  const [seen, setSeen] = useState(target)
  if (target.name !== seen.name || target.description !== seen.description) {
    const nameFollows = name === seen.name || name.trim() === target.name
    const descFollows = description === seen.description || description.trim() === target.description
    setSeen(target)
    if (nameFollows) setName(target.name)
    if (descFollows) setDescription(target.description)
  }
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const nameRef = useRef<HTMLInputElement>(null)
  // A 'name' request from the context menu lands here: focus the field once, then tell the panel it was handled.
  useEffect(() => {
    if (!focus) return
    nameRef.current?.focus(); onFocused?.()
  }, [focus, onFocused])
  // the server counts code points after trimming
  const nameLen = cpLength(name.trim()), descLen = cpLength(description.trim())
  const tooLong = nameLen > NAME_MAX || descLen > DESCRIPTION_MAX
  const savedName = saved?.name ?? '', savedDescription = saved?.description ?? ''
  const differs = name !== savedName || description !== savedDescription
  const edited = name !== target.name || description !== target.description
  const off = where === null
  // a field differs from the saved label, as Save's `differs` judges it: marked edited (RC1-31) and counted, so the marks
  // agree with Save (R6); a suggested name with no saved label is unsaved and is marked
  const nameEdited = name !== savedName, descEdited = description !== savedDescription
  const unsaved = unsavedText(Number(nameEdited) + Number(descEdited))

  // The saved label reaches every open browser (this one included) as a port_labels message, so nothing is updated here.
  async function save() {
    if (!where) return
    setBusy(true); setError(null)
    try { await putPortLabel({ server: where.server, key: where.key, name, description }) }
    catch (x) { setError(x instanceof ApiError ? x.message : String(x)) }
    finally { setBusy(false) }
  }
  function revert() { setName(target.name); setDescription(target.description); setError(null) }

  return (
    <div className="name-block">
      <h3>Name</h3>
      {loadError && <p role="status" className="error">The port names file could not be read ({loadError}); it is kept as it is.</p>}
      <p className="muted">{off ? 'Connect to name ports.' : `Shown in the port lists. Saved by this GUI for ${where.server}; clear both fields to remove the name.`}</p>
      <div className="form-grid">
        <div className="form-row">
          <label htmlFor="port-name-input">Name</label>
          <span><input id="port-name-input" ref={nameRef} value={name} disabled={off} onChange={(e) => setName(oneLine(e.target.value))} onPaste={pasteInto(setName)}
            className={nameEdited ? 'edited' : undefined} aria-describedby={nameEdited ? 'port-name-count port-name-ed' : 'port-name-count'} />
            {nameEdited && <EditedTag id="port-name-ed" />}<span id="port-name-count" className="muted"> {nameLen}/{NAME_MAX}</span></span>
        </div>
        <div className="form-row">
          <label htmlFor="port-desc-input">Description</label>
          <span><input id="port-desc-input" value={description} disabled={off} onChange={(e) => setDescription(oneLine(e.target.value))} onPaste={pasteInto(setDescription)}
            className={descEdited ? 'edited' : undefined} aria-describedby={descEdited ? 'port-desc-count port-desc-ed' : 'port-desc-count'} />
            {descEdited && <EditedTag id="port-desc-ed" />}<span id="port-desc-count" className="muted"> {descLen}/{DESCRIPTION_MAX}</span></span>
        </div>
      </div>
      {tooLong && <p role="alert" className="error">The name is limited to {NAME_MAX} characters and the description to {DESCRIPTION_MAX}.</p>}
      {error && <p role="alert" className="error">{error}</p>}
      <div className="dialog-footer">
        <button type="button" disabled={off || busy || tooLong || !differs} onClick={() => void save()}>Save</button>
        <button type="button" disabled={off || !edited} onClick={revert}>Revert</button>
        {unsaved && <span className="unsaved-count">{unsaved}</span>}
      </div>
    </div>
  )
}

// Names one port, in place. Naming edits the GUI's own file, so neither read-only nor ownership gates it.
export function PortNameField({ port, ports, focus, onFocused }: { port: PortView; ports: PortView[]; focus?: boolean; onFocused?: () => void }) {
  const { session, portLabelsError } = useAppState()
  const labelOf = usePortLabelOf(ports)
  const where = session.params ? portKey(session.params, port, ports) : null
  return <NameForm key={`${session.connect_seq}|${where?.server}|${where?.key}`} where={where} saved={labelOf(port)}
    suggestion={confirmedName(session.ifaces ?? [], port.id)} loadError={portLabelsError} focus={focus} onFocused={onFocused} />
}
