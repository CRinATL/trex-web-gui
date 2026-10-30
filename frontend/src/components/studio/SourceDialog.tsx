import { useEffect, useState } from 'react'
import { ApiError, profileSource } from '../../api'
import { useHotkey } from '../../keys'
import { useDialogFocus } from '../useDialogFocus'

// What the source route's refusals mean to a person (backend api/studio.py profile_source).
function problem(x: unknown): string {
  if (x instanceof ApiError && x.status === 404 && x.code === 'source_missing') return 'The source file is missing: it was moved or deleted on the backend host.'
  if (x instanceof ApiError || x instanceof Error) return x.message
  return String(x)
}

// "View source…" on a .py entry: the path the profile was imported from, then that file's text, read-only. Control
// assigns the .py itself with its tunables; edits in the table are not written back to it.
// `active` false (the page is hidden): Escape does nothing.
export function SourceDialog({ slug, onClose, active = true }: { slug: string; onClose: () => void; active?: boolean }) {
  const ref = useDialogFocus<HTMLDivElement>('[data-autofocus]')
  useHotkey('escape', () => { if (active) onClose() })
  const [source, setSource] = useState<{ path: string; text: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let live = true
    profileSource(slug).then((s) => { if (live) setSource(s) }, (x: unknown) => { if (live) setError(problem(x)) })
    return () => { live = false }
  }, [slug])
  return (
    <div role="dialog" aria-modal="true" aria-label="Profile source" className="modal source-dialog" ref={ref}>
      <h2>Profile source</h2>
      {error !== null && <p role="alert" className="error">{error}</p>}
      {error === null && source === null && <p className="muted">Reading the source…</p>}
      {source && (
        <>
          <p className="mono">{source.path}</p>
          <pre className="source-text" tabIndex={0}>{source.text}</pre>
        </>
      )}
      <div className="dialog-footer"><div className="end"><button type="button" data-autofocus onClick={onClose}>Close</button></div></div>
    </div>
  )
}
