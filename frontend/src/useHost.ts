import { useEffect, useState } from 'react'
import { getAbout } from './api'

// The name of the computer running trex-web-gui serve, from GET /api/about. One request per page load, shared by every caller and
// cached once it succeeds; a failed request resolves to null (callers show fallback copy), is not cached, and throws nothing.
let cached: string | null = null
let inflight: Promise<string | null> | null = null

function load(): Promise<string | null> {
  if (cached) return Promise.resolve(cached)
  if (!inflight) {
    inflight = getAbout().then((a) => { cached = a.host || null; inflight = null; return cached }, () => { inflight = null; return null })
  }
  return inflight
}

// For tests: forget the cached host.
export function resetHostCache() { cached = null; inflight = null }

export const HOST_FALLBACK = 'the computer running trex-web-gui serve'

export function useHost(): string | null {
  const [host, setHost] = useState<string | null>(cached)
  useEffect(() => {
    let live = true
    void load().then((h) => { if (live && h) setHost(h) })
    return () => { live = false }
  }, [])
  return host
}
