import type { FieldDef } from '../../api'

// A BITMASK field's groups: a group with one "set" value is a checkbox, any other group is a choice. Each commit is the whole new number.
export function BitFlags({ def, value, onCommit }: { def: FieldDef; value: number; onCommit: (value: number) => void }) {
  const change = (mask: number, bits: number) => onCommit((value & ~mask) | bits)
  return (
    <span className="bit-flags" role="group" aria-label={def.name}>
      {(def.bits ?? []).map((g) => {
        const set = g.values.find((v) => v.value !== 0)
        if (g.values.length === 2 && set) {
          return (
            <label key={`${g.mask}-${g.name}`} className="bit-flag">
              <input type="checkbox" checked={(value & g.mask) === set.value} onChange={(e) => change(g.mask, e.target.checked ? set.value : 0)} /> {g.name}
            </label>
          )
        }
        const current = value & g.mask
        return (
          <label key={`${g.mask}-${g.name}`} className="bit-flag">
            {g.name} <select value={current} onChange={(e) => change(g.mask, Number(e.target.value))}>
              {g.values.some((v) => v.value === current) ? null : <option value={current}>{`0x${current.toString(16)}`}</option>}
              {g.values.map((v) => <option key={v.value} value={v.value}>{v.name}</option>)}
            </select>
          </label>
        )
      })}
    </span>
  )
}
