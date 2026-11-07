import { useCallback, useEffect, useRef, useState } from 'react'
import { Navigate, NavLink, Route, Routes, useLocation, useMatch, useNavigate } from 'react-router-dom'
import { StatusBar } from './components/StatusBar'
import { LossDialog } from './components/LossDialog'
import { PlannedStopDialog } from './components/PlannedStopDialog'
import { MonitorStatus } from './components/monitor/MonitorStatus'
import { ConnectDialog } from './components/ConnectDialog'
import { useHotkey } from './keys'
import { CapturePage } from './pages/CapturePage'
import { ControlPage } from './pages/ControlPage'
import { EmuPage } from './pages/EmuPage'
import { EventsPage } from './pages/EventsPage'
import { DashboardPage } from './pages/DashboardPage'
import { MonitorPage } from './pages/MonitorPage'
import { SettingsPage } from './pages/SettingsPage'
import { EditorPage } from './pages/EditorPage'
import { useAppState } from './state'

// Shown instead of the shell: nothing on the page would work, and the WebSocket has stopped retrying.
function NotAuthorised() {
  return (
    <main className="not-authorised" role="alert">
      <h1>Not authorised</h1>
      <p>Open the URL printed by <code>trex-web-gui</code> on the backend host; through a tunnel, the same URL with <code>localhost</code>.</p>
    </main>
  )
}

export function AppShell() {
  const { backendOnline, authenticated, session, serverHint, serverPorts, ports, logUnseen, monitorOnly, helloSeen, appVersion } = useAppState()
  const [connectOpen, setConnectOpen] = useState(false)
  const navigate = useNavigate()
  const mainRef = useRef<HTMLElement>(null)
  const { pathname } = useLocation()
  // The page the user left may have held focus (a link or button that hid its own page): <main> takes it.
  // Not on first render, and not from an open dialog.
  // Keyed on the top-level page: a switch between the Editor's tabs (/editor, /editor/packet) keeps focus on the tab.
  const page = pathname.split('/')[1]
  const firstPath = useRef(true)
  useEffect(() => {
    if (firstPath.current) { firstPath.current = false; return }
    if (document.querySelector('[role="dialog"]')) return
    mainRef.current?.focus()
  }, [page])
  const onControl = useMatch('/control') !== null
  const onCapture = useMatch('/capture') !== null
  const onEditor = useMatch('/editor/*') !== null
  const editorTab = useMatch('/editor/packet') !== null ? 'packets' : 'profiles'
  // one owner for the dialog, so the shortcut works from any page
  const openConnect = useCallback(() => setConnectOpen(true), [])
  useHotkey('ctrl+shift+c', openConnect, !monitorOnly)
  useHotkey('ctrl+shift+p', useCallback(() => navigate('/settings'), [navigate]))
  if (!authenticated) return <NotAuthorised />
  // monitorOnly is known only from hello: until then mount nothing, or the Capture and Editor pages would ask a --monitor-only
  // backend for files it refuses (409) and the Control shell would flash on a wall display.
  if (!helloSeen) return <main className="connecting" role="status">Connecting…</main>
  // --monitor-only: no Control session exists, so nothing but the Monitor and the theme is offered, and the pages that would
  // ask the backend for session or capture state are not mounted (every write they make is refused with 409 anyway).
  if (monitorOnly) {
    return (
      <div className="shell">
        <header className="topbar">
          <strong>trex-web-gui</strong>
          <nav>
            <NavLink to="/monitor">Monitor</NavLink>
            <NavLink to="/settings">Settings</NavLink>
          </nav>
        </header>
        <main tabIndex={-1} ref={mainRef}>
          <Routes>
            <Route path="/monitor" element={<MonitorPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/monitor" replace />} />
          </Routes>
        </main>
        <footer className="statusbar">
          {backendOnline ? <MonitorStatus /> : <span role="status">Backend offline, reconnecting…</span>}
        </footer>
      </div>
    )
  }
  return (
    <div className="shell">
      <header className="topbar">
        <strong>trex-web-gui</strong>
        <nav>
          <NavLink to="/control">Control</NavLink>
          <NavLink to="/dashboard">Dashboard</NavLink>
          <NavLink to="/capture">Capture</NavLink>
          <NavLink to="/editor">Editor</NavLink>
          <NavLink to="/monitor">Monitor</NavLink>
          <NavLink to="/emu">EMU</NavLink>
          <NavLink to="/events">
            Events
            {logUnseen > 0 && <> <span className="badge badge-alert" aria-label={`${logUnseen} unseen errors and warnings`}>{logUnseen}</span></>}
          </NavLink>
          <NavLink to="/settings">Settings</NavLink>
        </nav>
      </header>
      <main tabIndex={-1} ref={mainRef}>
        {/* Mounted once, outside the routes, so selection, the inspected profile and tunables survive other pages; the Capture page likewise keeps its port choice and filter */}
        <div hidden={!onControl}><ControlPage active={onControl} onConnect={backendOnline ? openConnect : undefined} /></div>
        <div hidden={!onCapture}><CapturePage active={onCapture} /></div>
        <div hidden={!onEditor}><EditorPage tab={editorTab} active={onEditor} /></div>
        <Routes>
          <Route path="/" element={<Navigate to="/control" replace />} />
          <Route path="/control" element={null} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/capture" element={null} />
          <Route path="/events" element={<EventsPage />} />
          <Route path="/editor" element={null} />
          <Route path="/editor/packet" element={null} />
          <Route path="/studio" element={<Navigate to="/editor" replace />} />
          <Route path="/studio/packet" element={<Navigate to="/editor/packet" replace />} />
          <Route path="/monitor" element={<MonitorPage />} />
          <Route path="/emu" element={<EmuPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
      <StatusBar backendOnline={backendOnline} appVersion={appVersion} session={session} owned={ports.filter((p) => p.owned).map((p) => p.id)} onConnect={openConnect} />
      <LossDialog session={session} />
      <PlannedStopDialog session={session} />
      {connectOpen && <ConnectDialog initialHost={serverHint ?? '127.0.0.1'} initialPorts={serverPorts} initialParams={session.params} onClose={() => setConnectOpen(false)} />}
    </div>
  )
}
