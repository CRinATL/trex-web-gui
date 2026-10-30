import { ConfirmDialog } from '../daemon/ConfirmDialog'
import { ModalLayer } from '../packet/ModalLayer'

// "Discard the packet edits?", asked before an action that would drop the embedded editor's unsaved edits (R18).
// `onDiscard` runs the action; `onKeep` closes the question and leaves the edits.
export function DiscardEditsDialog({ body, onDiscard, onKeep }: { body: string; onDiscard: () => void; onKeep: () => void }) {
  return (
    <ModalLayer>
      <ConfirmDialog title="Discard the packet edits?" confirmLabel="Discard" cancelLabel="Keep editing" danger onClose={onKeep}
        body={<p>{body}</p>} onConfirm={async () => onDiscard()} />
    </ModalLayer>
  )
}
