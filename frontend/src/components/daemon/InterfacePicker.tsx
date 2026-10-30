import type { DeviceOption } from './configModel'

// One `interfaces` entry: a text box that suggests the daemon's devices and `dummy`. It stays free text because TRex also takes
// names that are not PCI slots (a veth, an af_packet name).
export function InterfacePicker({ id, label, value, devices, invalid, describedBy, onChange }: {
  id: string; label: string; value: string; devices: DeviceOption[]; invalid: boolean; describedBy?: string; onChange: (v: string) => void
}) {
  return (
    <>
      <input id={id} list={`${id}-devices`} value={value} aria-label={label} aria-invalid={invalid || undefined} aria-describedby={describedBy}
        onChange={(e) => onChange(e.target.value)} />
      <datalist id={`${id}-devices`}>
        {devices.map((d) => <option key={d.id} value={d.id} label={d.label} />)}
      </datalist>
    </>
  )
}
