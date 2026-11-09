import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, daemonConfigPreview, daemonConnect, daemonDisconnect } from '../../api'
import { useHotkey } from '../../keys'
import { useAppState } from '../../state'
import { Tabs, tabPanel } from '../../ui/Tabs'
import { useDialogFocus } from '../useDialogFocus'
import { ConfigTab } from './ConfigTree'
import { defaultsOf, readConnectInfo, type DaemonInfo, type Values } from './configModel'
import { getLastPushed, intIn, rememberPushed, type PushedConfig } from './daemonForm'
import { DaemonYamlTab } from './DaemonYamlTab'
import { DaemonLogsTab } from './DaemonLogsTab'
import { DaemonRunTab } from './DaemonRunTab'
import { usePreview } from './useConfigPreview'

type Tab = 'config' | 'yaml' | 'run' | 'logs'
const TABS: { id: Tab; label: string }[] = [{ id: 'config', label: 'Config' }, { id: 'yaml', label: 'YAML' }, { id: 'run', label: 'Run' }, { id: 'logs', label: 'Logs' }]
const DEFAULT_DAEMON_PORT = 8090
const errText = (x: unknown) => (x instanceof ApiError || x instanceof Error ? x.message : String(x))

// The last target a connect succeeded on, kept for the page's life so that opening the dialog again reconnects to it.
let lastTarget: { host: string; port: number } | null = null

// "v3.08" from the daemon's multi-line version text, else its first line.
function versionOf(text: string | null): string | null {
  if (!text) return null
  return /Version\s*:\s*(\S+)/.exec(text)?.[1] ?? text.split('\n')[0].trim()
}

// The TRex daemon dialog (spec 6.5): a connect bar over a status line, then Config, YAML, Run and Logs. While it is open the
// backend polls the daemon; closing it disconnects, so nothing polls behind a closed dialog (spec 6.4). The last pushed config
// (path and values) is kept outside the dialog (daemonForm getLastPushed), so a reopened dialog still prefills the Run tab from
// it; `initialPushed` overrides that.
export function DaemonDialog({ onClose, initialPushed }: { onClose: () => void; initialPushed?: PushedConfig | null }) {
  const { daemon, daemonHint, session } = useAppState()
  const ref = useDialogFocus<HTMLDivElement>('[data-autofocus]')
  const [host, setHost] = useState(lastTarget?.host ?? session.params?.host ?? daemonHint?.host ?? '')
  const [port, setPort] = useState(String(lastTarget?.port ?? daemonHint?.port ?? DEFAULT_DAEMON_PORT))
  const [tab, setTab] = useState<Tab>('run')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [, setPushedSeq] = useState(0)   // re-renders after a Push; the config itself is kept outside the dialog, per daemon
  const [info, setInfo] = useState<DaemonInfo | null>(null)   // what the last connect read: the metadata, devices, files dir
  const [values, setValues] = useState<Values | null>(null)    // the config being edited; null until a connect has read the metadata
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState('')
  const [modals, setModals] = useState({ run: false, yaml: false })
  const inFlight = useRef(false)
  const connected = daemon?.state === 'connected'
  const pushed = initialPushed ?? getLastPushed(daemon?.target ?? (connected ? null : lastTarget))

  const close = () => { void daemonDisconnect().catch(() => undefined); onClose() }
  const onRunModal = useCallback((open: boolean) => setModals((m) => ({ ...m, run: open })), [])
  const onYamlModal = useCallback((open: boolean) => setModals((m) => ({ ...m, yaml: open })), [])
  const confirmOpen = modals.run || modals.yaml
  useHotkey('escape', close, !confirmOpen)
  const preview = usePreview(editing ? { text } : values ? { values } : null)

  // A connect's reply: the form starts at the daemon's own config (its /etc/trex_cfg.yaml, parsed by the backend) when the
  // reply carries one that parses, else at the metadata's defaults (daemon-ui-1). It starts again only when the target or the
  // daemon's metadata differs from the one the form was started for: a reconnect to the same daemon keeps the form, another
  // daemon on the same TRex build does not (its config is its own). The defaults show at once; the parsed config replaces them
  // only if nothing was edited meanwhile (`v === seeded`: a later seed, for another target, is a new object, so a stale answer
  // never lands). A reseed also leaves Edit YAML and drops its text: the text is the old daemon's config too.
  const seedKey = useRef<string | null>(null)
  function adopt(reply: Record<string, unknown>, target: string) {
    const read = readConnectInfo(reply)
    if (!read) return
    setInfo(read)
    const key = `${target} ${read.metadataVersion}`
    if (seedKey.current === key) return
    seedKey.current = key
    const seeded = defaultsOf(read.metadata)
    setValues(seeded)
    setEditing(false); setText('')
    if (read.configYaml === '') return
    daemonConfigPreview({ text: read.configYaml }).then((r) => {
      const server = r.values
      if (server) setValues((v) => (v === seeded ? server : v))
    }, () => undefined)   // unread: the defaults stay, and Load from server shows the error
  }
  function pushedConfig(p: PushedConfig) { if (daemon?.target) rememberPushed(daemon.target, p); setPushedSeq((n) => n + 1) }
  function setEditingText(on: boolean) {
    if (on) setText(preview.result?.yaml ?? '')
    else if (preview.result?.values) setValues(preview.result.values)
    setEditing(on)
  }

  async function connect(h: string, p: number) {
    if (inFlight.current) return
    inFlight.current = true; setBusy(true); setError(null)
    try { adopt(await daemonConnect(h, p), `${h}:${p}`); lastTarget = { host: h, port: p } }
    catch (x) { setError(errText(x)) }
    finally { inFlight.current = false; setBusy(false) }
  }
  function submit() {
    const p = intIn(port, 1, 65535)
    if (!host.trim()) { setError('Host is required'); return }
    if (p === null) { setError('Port must be 1–65535'); return }
    void connect(host.trim(), p)
  }
  async function disconnect() {
    try { await daemonDisconnect(); setError(null) } catch (x) { setError(errText(x)) }
  }
  // Opening reconnects to the last target (once: a StrictMode remount keeps the ref).
  const started = useRef(false)
  useEffect(() => {
    if (started.current) return
    started.current = true
    // Not connected: reconnect to the last target. Connected but holding no metadata (this dialog made no connect): read it again.
    const target = connected ? daemon?.target ?? null : lastTarget
    if (target && (!connected || info === null) && !inFlight.current) {
      inFlight.current = true
      daemonConnect(target.host, target.port).then((r) => adopt(r, `${target.host}:${target.port}`)).catch((x) => setError(errText(x))).finally(() => { inFlight.current = false })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, on open
  }, [])

  const runningSince = daemon?.run?.state === 'running' ? new Date(daemon.run.started_at * 1000).toLocaleTimeString() : null
  const version = versionOf(daemon?.version ?? null)
  const stateText = daemon === null ? 'not connected' : daemon.state === 'error' ? `error: ${daemon.last_error ?? 'unknown'}` : daemon.state

  return (
    <div className="modal daemon-dialog" role="dialog" aria-modal="true" aria-labelledby="daemon-title" ref={ref}>
      <h2 id="daemon-title">TRex daemon</h2>
      <form className="daemon-connect" onSubmit={(e) => { e.preventDefault(); submit() }} noValidate>
        <label htmlFor="daemon-host">Host</label>
        <input id="daemon-host" value={host} onChange={(e) => setHost(e.target.value)} data-autofocus />
        <label htmlFor="daemon-port">Port</label>
        <input id="daemon-port" className="narrow" value={port} inputMode="numeric" onChange={(e) => setPort(e.target.value)} />
        <button type="submit" disabled={busy}>Connect</button>
        <button type="button" disabled={busy || !connected} onClick={() => void disconnect()}>Disconnect</button>
      </form>
      {/* One note, two states: the exposure warning until the daemon is connected, then a green reminder (user, 2026-11-07). */}
      {connected && daemon?.target
        ? <p role="note" className="banner ok">Connected to the TRex daemon at {daemon.target.host}:{daemon.target.port}. It runs as root and takes commands from anyone who can reach port {daemon.target.port}; keep that port restricted.</p>
        : <p role="note" className="banner">The TRex daemon runs as root with no authentication and binds 0.0.0.0 by default, and push_file and prepare_astf_profile let anyone who reaches port 8090 run code as root, whatever trex-web-gui does; restrict 8090 (firewall to the GUI host, or --trex-host 127.0.0.1 plus an SSH tunnel) before using daemon control on a shared network.</p>}
      {error && <p role="alert" className="error">{error}</p>}
      <p role="status" className="daemon-status">
        State: {stateText}{version && ` · ${version}`}{runningSince && ` · running since ${runningSince}`}
      </p>
      <Tabs label="Daemon" idPrefix="daemon" tabs={TABS} selected={tab} onSelect={setTab} />
      <div className="daemon-body">
        <div {...tabPanel('daemon', 'config', tab)}>
          {editing
            ? <p className="muted">The YAML is being edited as text. Switch off Edit YAML on the YAML tab to use the form.</p>
            : <ConfigTab info={info} values={values} errors={preview.failure === null ? preview.result?.errors ?? [] : []} onChange={setValues} />}
        </div>
        <div {...tabPanel('daemon', 'yaml', tab)}>
          <DaemonYamlTab preview={preview} editing={editing} text={text} canEdit={values !== null} connected={connected} target={daemon?.target ?? null}
            filesPath={daemon?.files_path ?? info?.filesPath ?? null} onEditing={setEditingText} onText={setText} onPushed={pushedConfig} onModal={onYamlModal} />
        </div>
        <div {...tabPanel('daemon', 'run', tab)}><DaemonRunTab daemon={daemon} pushed={pushed} onModal={onRunModal} /></div>
        <div {...tabPanel('daemon', 'logs', tab)}>{tab === 'logs' && <DaemonLogsTab connected={connected} />}</div>
      </div>
      <div className="dialog-footer"><div className="end"><button type="button" onClick={close}>Close</button></div></div>
    </div>
  )
}
