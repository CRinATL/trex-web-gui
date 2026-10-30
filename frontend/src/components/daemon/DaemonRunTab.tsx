import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, daemonLog, daemonStart, daemonStartPreview, daemonStop, monitorConnect, type DaemonMsg, type StartBody } from '../../api'
import { monitorStore } from '../../monitorStore'
import { useAppState, useDaemonActions } from '../../state'
import { Info } from '../../ui/Info'
import { HOST_FALLBACK, useHost } from '../../useHost'
import { OpenMonitorLink } from '../OpenMonitorLink'
import { ConfirmDialog } from './ConfirmDialog'
import { cmdPortPrefill, DEFAULT_CFG_PATH, intIn, monitorPortPrefill, NEW_STF_FORM, stfFields, type PushedConfig, type StfForm } from './daemonForm'
import { StfRunFields } from './StfRunFields'
import { RunButton } from '../RunButton'

const errText = (x: unknown) => (x instanceof ApiError || x instanceof Error ? x.message : String(x))
const CFG_PATH_RE = /^\/[A-Za-z0-9_./-]*$/   // the backend's cfg_path rule
const PREVIEW_DEBOUNCE_MS = 300
const LOG_TAIL_LINES = 30
const START_TIMEOUT_S = 40

type Confirm = 'start' | 'stop' | 'force' | 'force_ack'
const mmss = (s: number) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`

// The Run tab: the start form with its command preview, the daemon's status, a stateful run's countdown and final log, and
// Start, Stop and Force stop, each behind a confirmation. `onModal` tells the dialog a confirmation is open (so Escape closes only that).
export function DaemonRunTab({ daemon, pushed, onModal }: { daemon: DaemonMsg | null; pushed: PushedConfig | null; onModal: (open: boolean) => void }) {
  const gui = useHost()
  const { serverPorts } = useAppState()
  const { setLastStfRun } = useDaemonActions()
  const [cfgPath, setCfgPath] = useState(pushed?.path ?? DEFAULT_CFG_PATH)
  const [cores, setCores] = useState('')
  const [timeoutS, setTimeoutS] = useState(String(START_TIMEOUT_S))
  const [args, setArgs] = useState('')
  const [mode, setMode] = useState<'stl' | 'stf'>('stl')
  const [cmdPort, setCmdPort] = useState<string | null>(null)   // null: follows the prefill
  const [stf, setStf] = useState<StfForm>(NEW_STF_FORM)
  const [stfOk, setStfOk] = useState(false)
  const [stfModal, setStfModal] = useState(false)   // an upload dialog of the stateful fields is open
  const [preview, setPreview] = useState<{ key: string; text?: string; error?: string } | null>(null)
  const [confirm, setConfirm] = useState<Confirm | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [endsAt, setEndsAt] = useState<{ seq: number; ms: number } | null>(null)   // the end time belongs to one run
  const [startSnap, setStartSnap] = useState<{ body: StartBody; timeout: number; text: string } | null>(null)   // what the Start confirm shows and sends
  const [now, setNow] = useState(() => Date.now())
  const [finalLog, setFinalLog] = useState<{ seq: number; text: string } | null>(null)

  const connected = daemon?.state === 'connected'
  const host = daemon?.target?.host ?? ''
  const daemonPort = daemon?.target?.port ?? 0
  const run = daemon?.run ?? null
  const state = daemon?.status?.state ?? null
  const running = state !== null && state !== 'Idle'
  const cmdPortShown = cmdPort ?? String(cmdPortPrefill(cfgPath, pushed, serverPorts))
  const monitorPortShown = stf.monitorPort ?? String(monitorPortPrefill(cfgPath, pushed, serverPorts))

  const coresN = cores.trim() === '' ? undefined : intIn(cores, 1, 64)
  const timeoutN = intIn(timeoutS, 5, 300)
  const cfgBad = !CFG_PATH_RE.test(cfgPath) || cfgPath.split('/').includes('..')
  // The hidden command port of a stateful form may hold a bad value typed in Stateless mode: Stop then sends the prefill.
  const portText = (mode === 'stf' && intIn(cmdPortShown, 1, 65535) === null) ? String(cmdPortPrefill(cfgPath, pushed, serverPorts)) : cmdPortShown
  const problems = {
    cfg: cfgBad ? 'Config path must be absolute, with no .., using only letters, digits and _ . / -' : null,
    cores: coresN === null ? 'Cores must be a whole number, 1–64' : null,
    timeout: timeoutN === null ? 'Start timeout must be 5–300 s' : null,
    cmdPort: intIn(portText, 1, 65535) === null ? 'Command port must be 1–65535' : null,
  }
  const stfBody = mode === 'stf' ? stfFields(stf, monitorPortShown) : null
  const formOk = !problems.cfg && !problems.cores && !problems.timeout && (mode === 'stl' || (stfBody !== null && stfOk))
  const body: StartBody | null = !formOk || (mode === 'stf' && !stfBody) ? null : {
    cfg_path: cfgPath, options: coresN ? { c: coresN } : {}, trex_args: mode === 'stf' ? '' : args, mode,
    ...(stfBody ? { stf: stfBody } : {}),
  }
  const key = body ? JSON.stringify(body) : ''

  // The command preview, debounced; an answer for an older form is dropped.
  useEffect(() => {
    if (!body) return
    let live = true
    const timer = setTimeout(() => {
      daemonStartPreview(body).then((r) => { if (live) setPreview({ key, text: r.command_preview }) }, (x) => { if (live) setPreview({ key, error: errText(x) }) })
    }, PREVIEW_DEBOUNCE_MS)
    return () => { live = false; clearTimeout(timer) }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `key` is the serialised body
  }, [key])

  useEffect(() => { onModal(confirm !== null || stfModal) }, [confirm, stfModal, onModal])
  useEffect(() => () => onModal(false), [onModal])

  // A stateful run: its end time (the start reply's, else the daemon's record), ticking once a second while it runs.
  const stfRun = run?.mode === 'stf' ? run : null
  const stfLive = stfRun?.state === 'running'
  const endMs = endsAt && endsAt.seq === stfRun?.seq ? endsAt.ms : (stfRun && stfRun.duration_s !== null ? (stfRun.started_at + stfRun.duration_s) * 1000 : null)
  useEffect(() => {
    if (!stfLive) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [stfLive])
  // Ended or stopped: read TRex's log once for this run.
  const fetchedFor = useRef<number | null>(null)
  const stfSeq = stfRun?.seq ?? null
  const stfEnded = stfRun !== null && stfRun.state !== 'running'
  useEffect(() => {
    if (!connected || !stfEnded || stfSeq === null || fetchedFor.current === stfSeq) return
    fetchedFor.current = stfSeq
    // A failed read shows its error and is tried again the next time the dialog is connected (not on every render).
    daemonLog('trex').then((r) => setFinalLog({ seq: stfSeq, text: r.text }), (x) => { fetchedFor.current = null; setFinalLog({ seq: stfSeq, text: `Could not read the log: ${errText(x)}` }) })
  }, [connected, stfEnded, stfSeq])

  const onValid = useCallback((ok: boolean) => setStfOk(ok), [])
  const patchStf = useCallback((p: Partial<StfForm>) => setStf((f) => ({ ...f, ...p })), [])
  const setPath = (v: string) => { setCfgPath(v); setCmdPort(null); setStf((f) => ({ ...f, monitorPort: null })) }
  // A Push made while this tab is open points the config path at the pushed file (and so at its ports): state adjusted while rendering.
  const [pushedSeen, setPushedSeen] = useState(pushed)
  if (pushed !== pushedSeen) { setPushedSeen(pushed); if (pushed) setPath(pushed.path) }

  // Start: a stateful run also attaches the Monitor to its publisher and remembers when it ends.
  async function doStart() {
    if (!startSnap) return
    const { body, timeout } = startSnap
    const r = await daemonStart({ ...body, timeout, confirm: true })
    setConfirm(null); setStartSnap(null); setNote(null)
    if (r.run.mode !== 'stf' || !body.stf) return
    const ends = Date.now() + body.stf.d * 1000
    setEndsAt({ seq: r.run.seq, ms: ends }); setNow(Date.now()); setFinalLog(null); setLastStfRun({ host, endsAt: ends })
    const port = Number(monitorPortShown)
    const target = monitorStore.get()?.target
    if (!(target && target.host === host && target.port === port)) {
      monitorConnect(host, port).catch((x) => setNote(`The run started, but the Monitor could not attach: ${errText(x)}`))
    }
  }
  async function doStop(force: boolean, ack: boolean) {
    const r = await daemonStop({ rpc_port: Number(portText), confirm: true, force, owners_unknown_ack: ack })
    setConfirm(null)
    setNote(r.running ? 'TRex is still running after the stop.' : null)
  }
  // Force stop's owners_unknown asks once more; any other refusal stays in the first confirmation.
  async function doForce() {
    try { await doStop(true, false) }
    catch (x) {
      if (x instanceof ApiError && x.code === 'owners_unknown') { setConfirm('force_ack'); return }
      throw x
    }
  }

  const stopOk = connected && daemon?.running_seq != null && !problems.cmdPort
  const foreignStf = !!daemon && daemon.trex_cmds.some((c) => /(^|\s)-f(\s|$)/.test(c.cmd))   // the TRex on the host runs -f
  const canForce = connected && running && daemon?.running_seq == null
  const startOk = connected && !running && !daemon?.busy && body !== null && preview?.key === key && !!preview.text
  const tail = finalLog && finalLog.seq === stfRun?.seq ? finalLog.text.split('\n').slice(-LOG_TAIL_LINES).join('\n') : null
  const left = endMs === null ? null : Math.max(0, Math.ceil((endMs - now) / 1000))

  return (
    <div className="daemon-run">
      <section aria-labelledby="daemon-run-config">
        <h3 id="daemon-run-config">Configuration</h3>
        <div className="form-grid">
          <Field id="run-cfg" label="Config path" error={problems.cfg}><input id="run-cfg" value={cfgPath} onChange={(e) => setPath(e.target.value)} /></Field>
          <Field id="run-cores" label="Cores (-c)" error={problems.cores}>
            <input id="run-cores" className="narrow" value={cores} inputMode="numeric" placeholder="default" onChange={(e) => setCores(e.target.value)} />
          </Field>
          <Field id="run-timeout" label="Start timeout (s)" error={problems.timeout}>
            <input id="run-timeout" className="narrow" value={timeoutS} inputMode="numeric" onChange={(e) => setTimeoutS(e.target.value)} />
          </Field>
          <div className="form-row">
            <span className="row-label" id="run-mode-label">Mode</span>
            <span role="radiogroup" aria-labelledby="run-mode-label">
              <label><input type="radio" name="run-mode" checked={mode === 'stl'} onChange={() => setMode('stl')} /> Stateless (interactive, -i)</label>{' '}
              <label><input type="radio" name="run-mode" checked={mode === 'stf'} onChange={() => setMode('stf')} /> Stateful run (-f)</label>
            </span>
          </div>
          {mode === 'stl' && (
            <>
              <div className="form-row">
                <label htmlFor="run-args">Extra arguments</label>
                <span>
                  <input id="run-args" value={args} onChange={(e) => setArgs(e.target.value)} />
                  <Info label="extra arguments">Up to 20 space-separated tokens made of letters, digits and <code>_ . , : = / + -</code> (no shell syntax). <code>--cfg</code>, <code>-i</code>, <code>--astf</code>, <code>-f</code>, <code>-d</code>, <code>-m</code>, <code>-l</code>, <code>--rx-check</code> and <code>--client_cfg</code> are refused, also abbreviated; the fields here set what they would.</Info>
                </span>
              </div>
              <Field id="run-cmd-port" label="TRex command port" error={problems.cmdPort}
                info={<Info label="the command port">the port {gui ?? HOST_FALLBACK} reaches the daemon's TRex on; Stop reads its port owners first</Info>}>
                <input id="run-cmd-port" className="narrow" value={cmdPortShown} inputMode="numeric" onChange={(e) => setCmdPort(e.target.value)} />
              </Field>
            </>
          )}
        </div>
        {mode === 'stf' && <StfRunFields form={stf} onChange={patchStf} cfgPath={cfgPath} pushed={pushed} serverPorts={serverPorts} onValid={onValid}
          upload={connected && daemon?.files_path ? { daemon: `${host}:${daemonPort}`, filesPath: daemon.files_path } : null} onModal={setStfModal} />}
      </section>

      <section aria-labelledby="daemon-run-preview">
        <h3 id="daemon-run-preview">Command preview</h3>
        {preview?.text && preview.key === key
          ? <code className="command-preview">{preview.text}</code>
          : preview?.error && preview.key === key ? <p role="status" className="error">{preview.error}</p>
          : <p className="muted">{body ? 'Reading the command…' : 'Complete the form to see the command.'}</p>}
      </section>

      <section aria-labelledby="daemon-run-status">
        <h3 id="daemon-run-status">Status</h3>
        {!connected ? <p className="muted">Not connected to a daemon.</p> : (
          <>
            <dl>
              <dt>is_running</dt><dd>{running ? `yes (${state})` : 'no'}</dd>
              <dt>running_seq</dt><dd>{daemon?.running_seq ?? 'none'}</dd>
            </dl>
            {daemon && daemon.trex_cmds.length > 0
              ? <ul aria-label="TRex processes on the host">{daemon.trex_cmds.map((c) => <li key={c.pid}>pid {c.pid}: <code>{c.cmd}</code></li>)}</ul>
              : <p className="muted">No TRex process on the host.</p>}
            <p className="muted">A TRex the daemon did not start blocks Start.</p>
          </>
        )}
      </section>

      {stfRun && (
        <section aria-labelledby="daemon-run-stf">
          <h3 id="daemon-run-stf">Stateful run</h3>
          <p>{stfRun.profile}{stfRun.duration_s !== null && ` for ${stfRun.duration_s} s`}</p>
          {stfLive && left !== null && <p role="timer">ends in about {mmss(left)}</p>}
          {stfEnded && <p>{stfRun.state === 'stopped' ? 'Stopped' : `Ended: ${stfRun.outcome ?? ''}`}</p>}
          <p><OpenMonitorLink /></p>
          {tail !== null && (<><h4>Final log</h4><pre className="log-tail">{tail}</pre></>)}
        </section>
      )}

      {note && <p role="status" className="muted">{note}</p>}
      <div className="dialog-footer daemon-actions" role="group" aria-label="Run actions">
        <RunButton kind="go" lit={running} litLabel="TRex running" disabled={!startOk} onClick={() => { if (body && timeoutN !== null && preview?.text) { setStartSnap({ body, timeout: timeoutN, text: preview.text }); setConfirm('start') } }}>Start</RunButton>
        <RunButton kind="stop" disabled={!stopOk} onClick={() => setConfirm('stop')}>Stop</RunButton>
        {canForce && <RunButton kind="stop" onClick={() => setConfirm('force')}>Force stop</RunButton>}
      </div>

      {confirm === 'start' && startSnap && (
        <ConfirmDialog title="Start TRex" confirmLabel="Start" onConfirm={doStart} onClose={() => { setConfirm(null); setStartSnap(null) }}
          body={(
            <>
              <p>Start TRex on {host} (daemon port {daemonPort}) with this command?</p>
              <code className="command-preview">{startSnap.text}</code>
              {startSnap.body.stf && <p>It runs as root on {host} for {startSnap.body.stf.d} s. A stateful run has no command port, so Control cannot connect to it; watch it on the Monitor.</p>}
            </>
          )} />
      )}
      {confirm === 'stop' && (
        <ConfirmDialog title="Stop TRex" confirmLabel="Stop" onConfirm={() => doStop(false, false)} onClose={() => setConfirm(null)}
          body={(
            <>
              <p>Stop the TRex this GUI started on {host}? Every client connected to it, including this GUI&apos;s session, loses its connection.</p>
              <p className="muted">Daemon {host}:{daemonPort}, command port {portText}.</p>
            </>
          )} />
      )}
      {confirm === 'force' && (
        <ConfirmDialog title="Force stop TRex" confirmLabel="Force stop" danger onConfirm={doForce} onClose={() => setConfirm(null)}
          body={(
            <>
              <p>This TRex was not started by this GUI. Force stop kills the daemon&apos;s TRex on {host} whoever started it; it is still refused while another user owns a port.</p>
              <p className="muted">Daemon {host}:{daemonPort}, command port {portText}.</p>
            </>
          )} />
      )}
      {confirm === 'force_ack' && (
        <ConfirmDialog title="Force stop anyway?" confirmLabel="Force stop anyway" danger onConfirm={() => doStop(true, true)} onClose={() => setConfirm(null)}
          body={foreignStf
            ? <p>A stateful (-f) run has no command port, so the GUI cannot see who is using it. Another user may be running traffic on this TRex. Force stop anyway?</p>
            : <p>TRex&apos;s command port did not answer, so the GUI cannot see who owns its ports. Another user may be running traffic on this TRex. Force stop anyway?</p>} />
      )}
    </div>
  )
}

// One labelled form row with its optional ⓘ and an inline error.
function Field({ id, label, error, info, children }: { id: string; label: string; error: string | null; info?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="form-row">
      <label htmlFor={id}>{label}</label>
      <span>{children}{info}{error && <span className="error"> {error}</span>}</span>
    </div>
  )
}
