import { useEffect, useState } from 'react'
import { ApiError, daemonConfigPreview, type ConfigPreview } from '../../api'
import type { Values } from './configModel'

const PREVIEW_DEBOUNCE_MS = 300

export type PreviewBody = { values: Values } | { text: string } | null
// `ready`: the answer (or failure) is for the body as it stands now. `result` is the latest answer, which may be for an older body.
export interface PreviewState { result: ConfigPreview | null; failure: string | null; ready: boolean }

// The backend's rendering and checking of the config being edited (the form's values, or the YAML text), 300 ms after the
// last change. An answer for an older body is dropped. Anything that sends the YAML must wait for `ready`.
export function usePreview(body: PreviewBody): PreviewState {
  const key = body ? JSON.stringify(body) : ''
  const [answer, setAnswer] = useState<{ key: string; result?: ConfigPreview; failure?: string } | null>(null)
  const [latest, setLatest] = useState<ConfigPreview | null>(null)   // kept across a newer body's wait, so inline errors do not flicker
  useEffect(() => {
    if (!body) return
    let live = true
    const timer = setTimeout(() => {
      daemonConfigPreview(body).then(
        (result) => { if (live) { setAnswer({ key, result }); setLatest(result) } },
        (x) => { if (live) setAnswer({ key, failure: x instanceof ApiError || x instanceof Error ? x.message : String(x) }) },
      )
    }, PREVIEW_DEBOUNCE_MS)
    return () => { live = false; clearTimeout(timer) }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `key` is the serialised body
  }, [key])
  const current = body !== null && answer?.key === key ? answer : null
  return { result: latest, failure: current?.failure ?? null, ready: current !== null }
}
