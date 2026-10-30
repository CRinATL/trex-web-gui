import type { PacketDoc } from '../../api'
import { useHotkey } from '../../keys'
import { useDialogFocus } from '../useDialogFocus'
import { Presets } from './Presets'

// The presets in a modal. `active` false (another dialog is on top, or the page is hidden): Escape is not this dialog's.
export function PresetsDialog({ onOpen, onClose, active }: { onOpen: (doc: PacketDoc, flags: number | null) => void; onClose: () => void; active: boolean }) {
  const ref = useDialogFocus<HTMLDivElement>('[data-autofocus]')
  useHotkey('escape', () => { if (active) onClose() })
  return (
    <div role="dialog" aria-modal="true" aria-label="Presets" className="modal packet-dialog presets" ref={ref}>
      <h2>Presets</h2>
      <Presets onOpen={onOpen} />
      <div className="dialog-footer"><div className="end"><button type="button" data-autofocus onClick={onClose}>Close</button></div></div>
    </div>
  )
}
