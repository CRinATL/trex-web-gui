import { useSyncExternalStore } from 'react'
import { dashboardPrefs } from '../dashboardPrefs'

// Fit: the chart's own y range. From 0: y runs from 0 to the largest shown value. One setting for every Dashboard tab.
export function YAxisToggle() {
  const { yFrom0 } = useSyncExternalStore(dashboardPrefs.subscribe, dashboardPrefs.get)
  return (
    <span role="group" aria-label="Y axis" className="y-axis-toggle">
      Y axis
      <button type="button" className="chip" aria-pressed={!yFrom0} onClick={() => dashboardPrefs.set({ yFrom0: false })}>Fit</button>
      <button type="button" className="chip" aria-pressed={yFrom0} onClick={() => dashboardPrefs.set({ yFrom0: true })}>From 0</button>
    </span>
  )
}
