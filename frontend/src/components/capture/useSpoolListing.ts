import { useCallback, useEffect, useRef, useState } from 'react'
import { captureFiles, type SpoolListing } from '../../api'
import { errText } from '../../portGate'
import { useAppState } from '../../state'

export interface SpoolSource { listing: SpoolListing | null; error: string | null; reload: () => void }

// The backend's spool listing, shared by the Recorders panel (a partial file means Resume) and the file list.
// It is read on mount, again when a Save's file or error changes, and on reload(). A read that finishes after a newer one
// started is ignored, so an older listing never replaces a newer one.
export function useSpoolListing(): SpoolSource {
  const { captureSave } = useAppState()
  const [listing, setListing] = useState<SpoolListing | null>(null)
  const [error, setError] = useState<string | null>(null)
  const seq = useRef(0)
  const outcome = Object.values(captureSave).map((m) => `${m.id}:${m.file?.name ?? ''}:${m.error ?? ''}`).join('|')

  const reload = useCallback(() => {
    const mine = ++seq.current
    captureFiles()
      .then((l) => { if (mine === seq.current) { setListing(l); setError(null) } })
      .catch((e) => { if (mine === seq.current) setError(errText(e)) })
  }, [])
  useEffect(() => { reload() }, [reload, outcome])
  useEffect(() => () => { seq.current = -1 }, [])    // after unmount no read matches, so none sets state
  return { listing, error, reload }
}
