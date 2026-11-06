import { DropMenu } from '../../ui/DropMenu'

// "Setups ▾": a button that opens a two-item menu. The page owns the dialogs.
export function SetupsMenu({ connected, onSave, onManage }: { connected: boolean; onSave: () => void; onManage: () => void }) {
  return (
    <DropMenu name="Setups" items={[
      { id: 'save', label: 'Save current…', disabled: !connected, onPick: onSave },
      { id: 'manage', label: 'Manage setups…', onPick: onManage },
    ]} />
  )
}
