import { useState, type ReactNode } from 'react'
import { ApiError, daemonConfigPreview, type ConfigError } from '../../api'
import { Info } from '../../ui/Info'
import { addItem, blankItem, defaultsOf, fieldError, getAt, isAdvanced, NO_DEFAULT, pathKey, removeItem, setAt, type DaemonInfo, type DeviceOption, type MetaField, type Path, type Values } from './configModel'
import { InterfacePicker } from './InterfacePicker'

interface TreeProps { values: Values; errors: ConfigError[]; devices: DeviceOption[]; onChange: (v: Values) => void }
const idOf = (path: Path) => `cfg-${pathKey(path).replace(/\./g, '-')}`
const hasErrorUnder = (errors: ConfigError[], path: Path) => errors.some((e) => e.path === pathKey(path) || e.path.startsWith(`${pathKey(path)}.`))

// A fold that keeps its own open state: a problem inside opens it, and clearing the problem never closes it under the user.
function Fold({ className, summary, forceOpen, children }: { className: string; summary: string; forceOpen: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  if (forceOpen && !open) setOpen(true)
  return <details className={className} open={open} onToggle={(e) => setOpen(e.currentTarget.open)}><summary>{summary}</summary>{children}</details>
}

// One field. A scalar is a label and a control in the grid's two columns; an OBJECT is a fold; a LIST is a group with Add
// and a Remove on every entry. `label` is the field's own name, or "<item name> <n>" for a list entry.
function Field({ field, path, label, entry, ...t }: TreeProps & { field: MetaField; path: Path; label: string; entry?: { remove: () => void } }) {
  const { values, errors, devices, onChange } = t
  const id = idOf(path)
  const value = getAt(values, path)
  const error = fieldError(errors, path)
  // An entry of a list has no key to unset, so a cleared box keeps an empty value there; a keyed field is unset (the daemon's default applies).
  const set = (v: unknown) => onChange(setAt(values, path, v))
  const clearable = field.id !== undefined
  // The metadata's default as a hint, except where Load default leaves the field to TRex (NO_DEFAULT: `c` hints 4, TRex uses 1).
  const hint = field.default === undefined || (field.id !== undefined && NO_DEFAULT.has(field.id)) ? undefined : String(field.default)
  const note: ReactNode = (
    <>
      {field.description && <Info label={label}>{field.description}</Info>}
      {error && <span className="field-error error" id={`${id}-err`}><span aria-hidden="true">⚠ </span>{error}</span>}
    </>
  )
  const common = { id, 'aria-invalid': error ? true : undefined, 'aria-describedby': error ? `${id}-err` : undefined, 'aria-required': field.mandatory || undefined }
  const row = (control: ReactNode) => <div className="form-row"><label htmlFor={id}>{label}</label><span className="cfg-control">{control}{note}{entry && <button type="button" aria-label={`Remove ${label}`} onClick={entry.remove}>Remove</button>}</span></div>

  switch (field.type) {
    case 'NUMBER': case 'FLOAT':
      return row(<input {...common} type="number" step={field.type === 'NUMBER' ? 1 : 'any'} value={typeof value === 'number' ? value : ''} placeholder={hint}
        onChange={(e) => set(e.target.value === '' ? (clearable ? undefined : 0) : Number(e.target.value))} />)
    case 'BOOLEAN':
      return row(<input {...common} type="checkbox" checked={typeof value === 'boolean' ? value : field.default === true} onChange={(e) => set(e.target.checked)} />)
    case 'OBJECT': {
      const attrs = field.attributes ?? []
      return (
        <Fold className="full cfg-fold" summary={label} forceOpen={hasErrorUnder(errors, path)}>
          <div className="form-grid">
            {(field.description || entry) && (
              <div className="full cfg-fold-tools">
                {field.description && <Info label={label}>{field.description}</Info>}
                {entry && <button type="button" aria-label={`Remove ${label}`} onClick={entry.remove}>Remove</button>}
              </div>
            )}
            {attrs.map((a) => <Field key={a.id ?? a.name} {...t} field={a} path={[...path, a.id ?? a.name]} label={a.name} />)}
          </div>
        </Fold>
      )
    }
    case 'LIST': {
      const list = Array.isArray(value) ? value : []
      const item = field.item
      return (
        <fieldset className="full cfg-list" aria-label={label}>
          <legend>{label}{field.description && <Info label={label}>{field.description}</Info>}</legend>
          {error && <span className="field-error error" id={`${id}-err`}><span aria-hidden="true">⚠ </span>{error}</span>}
          {item && list.map((_, i) => (
            <div className="cfg-entry" key={i}>
              {field.id === 'interfaces' && item.type === 'STRING'
                ? <EntryPicker {...t} path={[...path, i]} label={`${item.name} ${i + 1}`} devices={devices} remove={() => onChange(removeItem(values, path, i))} />
                : <div className="form-grid"><Field {...t} field={item} path={[...path, i]} label={`${item.name} ${i + 1}`} entry={{ remove: () => onChange(removeItem(values, path, i)) }} /></div>}
            </div>
          ))}
          {item && <button type="button" onClick={() => onChange(addItem(values, path, blankItem(item)))}>Add {item.name.toLowerCase()}</button>}
        </fieldset>
      )
    }
    default:   // STRING, IP, MAC
      return row(<input {...common} type="text" value={typeof value === 'string' ? value : ''} placeholder={hint}
        onChange={(e) => set(e.target.value === '' && clearable ? undefined : e.target.value)} />)
  }
}

// An `interfaces` entry: the picker, its error and a Remove.
function EntryPicker({ path, label, remove, ...t }: TreeProps & { path: Path; label: string; remove: () => void }) {
  const id = idOf(path)
  const value = getAt(t.values, path)
  const error = fieldError(t.errors, path)
  return (
    <div className="cfg-control">
      <InterfacePicker id={id} label={label} value={typeof value === 'string' ? value : ''} devices={t.devices} invalid={error !== null} describedBy={error ? `${id}-err` : undefined}
        onChange={(v) => t.onChange(setAt(t.values, path, v))} />
      {error && <span className="field-error error" id={`${id}-err`}><span aria-hidden="true">⚠ </span>{error}</span>}
      <button type="button" aria-label={`Remove ${label}`} onClick={remove}>Remove</button>
    </div>
  )
}

// The config as a form: the default fields in a `.form-grid`, the rest under Advanced (spec §10). A fold holding a problem is open.
export function ConfigTree({ fields, values, errors, devices, onChange }: TreeProps & { fields: MetaField[] }) {
  const t = { values, errors, devices, onChange }
  const main = fields.filter((f) => !isAdvanced(f))
  const advanced = fields.filter(isAdvanced)
  const advancedError = advanced.some((f) => f.id !== undefined && hasErrorUnder(errors, [f.id]))
  const render = (f: MetaField) => <Field key={f.id ?? f.name} {...t} field={f} path={[f.id ?? f.name]} label={f.name} />
  return (
    <div className="cfg-tree">
      <div className="form-grid">{main.map(render)}</div>
      {advanced.length > 0 && (
        <Fold className="cfg-advanced" summary="Advanced" forceOpen={advancedError}>
          <div className="form-grid">{advanced.map(render)}</div>
        </Fold>
      )}
    </div>
  )
}

// The Config tab: its action bar (Load default, Load from server) over the tree.
export function ConfigTab({ info, values, errors, onChange }: { info: DaemonInfo | null; values: Values | null; errors: ConfigError[]; onChange: (v: Values) => void }) {
  const [problem, setProblem] = useState<string | null>(null)
  if (info === null || values === null) return <p className="muted">Connect to a daemon to edit its config; the fields come from the daemon.</p>
  async function loadServer() {
    setProblem(null)
    try {
      const r = await daemonConfigPreview({ text: info!.configYaml })
      if (r.values) onChange(r.values)
      else setProblem(`The daemon's config could not be read: ${r.errors[0]?.message ?? 'not a config'}`)
    } catch (x) { setProblem(x instanceof ApiError || x instanceof Error ? x.message : String(x)) }
  }
  return (
    <div className="cfg-tab">
      <div className="action-bar">
        <button type="button" onClick={() => { setProblem(null); onChange(defaultsOf(info.metadata)) }}>Load default</button>
        <button type="button" disabled={info.configYaml === ''} onClick={() => void loadServer()}>Load from server</button>
      </div>
      {problem && <p role="alert" className="error">{problem}</p>}
      <ConfigTree fields={info.metadata} values={values} errors={errors} devices={info.devices} onChange={onChange} />
    </div>
  )
}
