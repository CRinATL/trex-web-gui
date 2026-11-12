import type { PortView } from '../api'
import { readiness, TRAIL, useReadinessStrip, type Step } from '../readiness'

// The trail word that carries aria-current for a step; running stays on Start, blocked carries none.
const CURRENT: Record<Step, Step | null> = { acquire: 'acquire', assign: 'assign', start: 'start', running: 'start', blocked: null }

// One muted line above the Control tabs: Acquire → Assign a profile → Start with the current step marked, and the
// sentence for the port. A plain paragraph, not a live region: a polling PortView would re-announce on every update.
export function ReadinessStrip({ port, readOnly }: { port: PortView; readOnly: boolean }) {
  const { shown, setShown } = useReadinessStrip()
  if (!shown) return null
  const { step, text } = readiness(port, readOnly)
  return (
    <div className="readiness-strip">
      <ol className="readiness-trail">
        {TRAIL.map((t) => <li key={t.step} aria-current={CURRENT[step] === t.step ? 'step' : undefined}>{t.word}</li>)}
      </ol>
      <p className="readiness">{text}</p>
      <button type="button" onClick={() => setShown(false)}>Hide readiness strip</button>
    </div>
  )
}
