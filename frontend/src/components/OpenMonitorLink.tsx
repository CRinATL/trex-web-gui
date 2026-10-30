import { useOptionalNavigate } from '../routerSafe'

// A link to the Monitor page. Without a router (component tests) it is a plain link; `onNavigate` runs before a client-side move.
export function OpenMonitorLink({ onNavigate }: { onNavigate?: () => void }) {
  const navigate = useOptionalNavigate()
  return (
    <a href="/monitor" onClick={(e) => {
      if (!navigate) return
      e.preventDefault(); onNavigate?.(); navigate('/monitor')
    }}>Open Monitor</a>
  )
}
