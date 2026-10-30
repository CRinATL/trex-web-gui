import type { Tunable } from '../api'

// A profile's tunables as a form (moved out of TrafficTab, reused by the Studio's .py import). Checked always means
// "this flag is sent"; a choice whose default is not among its choices offers an empty "(default)" option.
export function TunablesForm({ specs, values, extra, onValues, onExtra }: {
  specs: Tunable[]; values: Record<string, unknown>; extra: string
  onValues: (values: Record<string, unknown>) => void; onExtra: (extra: string) => void
}) {
  const set = (dest: string, v: unknown) => onValues({ ...values, [dest]: v })
  return (
    <form className="tunables" onSubmit={(e) => e.preventDefault()}>
      {specs.map((t) => {
        const label = t.flag || t.dest
        const v = values[t.dest]
        return (
          <label key={t.dest} title={t.help} className={t.kind === 'bool_true' || t.kind === 'bool_false' ? 'check' : undefined}>
            {t.kind === 'bool_true' || t.kind === 'bool_false'
              // checked always means "this flag is sent": store_false sends its flag when the value is false
              ? <><input type="checkbox" checked={t.kind === 'bool_false' ? v === false : v === true}
                  onChange={(e) => set(t.dest, t.kind === 'bool_false' ? !e.target.checked : e.target.checked)} /> {label}</>
              : t.kind === 'choice'
                ? <>{label} <select value={String(v ?? '')} onChange={(e) => set(t.dest, e.target.value)}>
                    {!(t.choices ?? []).some((c) => String(c) === String(t.default)) && <option value="">(default)</option>}
                    {(t.choices ?? []).map((c) => <option key={String(c)} value={String(c)}>{String(c)}</option>)}</select></>
                : <>{label} <input type={t.kind === 'int' || t.kind === 'float' ? 'number' : 'text'} step={t.kind === 'float' ? 'any' : undefined}
                    value={String(v ?? '')} onChange={(e) => set(t.dest, e.target.value)} /></>}
          </label>
        )
      })}
      <label>Extra arguments <input type="text" value={extra} onChange={(e) => onExtra(e.target.value)} /></label>
    </form>
  )
}
