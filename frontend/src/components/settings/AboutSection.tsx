import { useEffect, useState } from 'react'
import { getAbout, type About } from '../../api'
import { useAppState } from '../../state'

// The About section of Settings: version, TRex client pin, supported server, the data directory and a paragraph on the
// backend's limits (login URL access, loopback-only listening).
// `dataDir` is the data directory from the settings GET, used until a settings message supplies one.
export function AboutSection({ dataDir }: { dataDir?: string | null }) {
  const [about, setAbout] = useState<About | null>(null)
  const [error, setError] = useState<string | null>(null)
  const dir = useAppState().settings?.data_dir ?? dataDir
  useEffect(() => { getAbout().then(setAbout).catch((e: Error) => setError(e.message)) }, [])
  return (
    <div>
      <h2>About trex-web-gui</h2>
      {error && <p className="error">{error}</p>}
      <dl>
        {about && <>
          <dt>Version</dt><dd>{about.app_version}</dd>
          <dt>TRex client pin</dt><dd>{about.trex_pin.upstream_sha} ({about.trex_pin.patches.length} vendor patches)</dd>
          <dt>Supported server</dt><dd>{about.supported}</dd>
        </>}
        {dir && <><dt>Data directory (on the backend host)</dt><dd><code>{dir}</code></dd></>}
      </dl>
      <p className="about-limits">
        This backend accepts a browser only through the login URL it printed at start; anything that can read that terminal or journal can use it.{' '}
        It listens on 127.0.0.1 only: from another machine, use an SSH tunnel whose local port equals the serving port.
      </p>
    </div>
  )
}
