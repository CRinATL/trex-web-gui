import { useInRouterContext, useLocation, useNavigate, type Location, type NavigateFunction } from 'react-router-dom'

// The Control page, the Traffic tab and the Studio page are rendered on their own in many tests, without a router; in
// the app they always sit inside one. Whether a router exists never changes during a component's life, so the
// conditional hook call below is stable.
export function useOptionalNavigate(): NavigateFunction | null {
  const inRouter = useInRouterContext()
  // eslint-disable-next-line react-hooks/rules-of-hooks
  return inRouter ? useNavigate() : null
}

export function useOptionalLocation(): Location | null {
  const inRouter = useInRouterContext()
  // eslint-disable-next-line react-hooks/rules-of-hooks
  return inRouter ? useLocation() : null
}
