import { useCallback, useEffect, useRef, useState } from 'react'
import { Navigate, NavLink, Route, Routes, useLocation, useMatch, useNavigate } from 'react-router-dom'
import { StatusBar } from './components/StatusBar'
import { MonitorStatus } from './components/monitor/MonitorStatus'
import { ConnectDialog } from './components/ConnectDialog'
import { useHotkey } from './keys'
import { CapturePage } from './pages/CapturePage'
import { ControlPage } from './pages/ControlPage'
import { EventsPage } from './pages/EventsPage'
import { DashboardPage } from './pages/DashboardPage'
import { MonitorPage } from './pages/MonitorPage'
import { PacketPage } from './pages/PacketPage'
import { SettingsPage } from './pages/SettingsPage'
import { StudioPage } from './pages/StudioPage'
import { useAppState } from './state'

// Shown instead of the shell: nothing on the page would work, and the WebSocket has stopped retrying.
function NotAuthorised() {
  return (
    <main className="not-authorised" role="alert">
      <h1>Not authorised</h1>
      <p>Open the URL printed by <code>trex-web-gui serve</code> on the backend host; through a tunnel, the same URL with <code>localhost</code>.</p>
    </main>
  )
}

export function AppShell() {
  const { backendOnline, authenticated, session, serverHint, serverPorts, ports, logUnseen, monitorOnly, helloSeen } = useAppState()
  const [connectOpen, setConnectOpen] = useState(false)
  const navigate = useNavigate()
  const mainRef = useRef<HTMLElement>(null)
  const { pathname } = useLocation()
  // The page the user left may have held focus (a link or button that hid its own page): <main> takes it.
  // Not on first render, and not from an open dialog.
  const firstPath = useRef(true)
  useEffect(() => {
    if (firstPath.current) { firstPath.current = false; return }
    if (document.querySelector('[role="dialog"]')) return
    mainRef.current?.focus()
  }, [pathname])
  const onControl = useMatch('/control') !== null
  const onCapture = useMatch('/capture') !== null
  const onStudio = useMatch('/studio') !== null
  const onPacket = useMatch('/studio/packet') !== null
  // The packet editor asks the backend for its definitions as it mounts, so it is mounted at the first visit and kept (hidden) after.
  const [packetSeen, setPacketSeen] = useState(false)
  if (onPacket && !packetSeen) setPacketSeen(true)
  // one owner for the dialog, so the shortcut works from any page
  const openConnect = useCallback(() => setConnectOpen(true), [])
  useHotkey('ctrl+shift+c', openConnect, !monitorOnly)
  useHotkey('ctrl+shift+p', useCallback(() => navigate('/settings'), [navigate]))
  if (!authenticated) return <NotAuthorised />
  // monitorOnly is known only from hello: until then mount nothing, or the Capture and Studio pages would ask a --monitor-only
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
          <NavLink to="/studio">Studio</NavLink>
          <NavLink to="/monitor">Monitor</NavLink>
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
        <div hidden={!onStudio}><StudioPage active={onStudio} /></div>
        {packetSeen && <div hidden={!onPacket}><PacketPage active={onPacket} /></div>}
        <Routes>
          <Route path="/" element={<Navigate to="/control" replace />} />
          <Route path="/control" element={null} />
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/capture" element={null} />
          <Route path="/events" element={<EventsPage />} />
          <Route path="/studio" element={null} />
          <Route path="/studio/packet" element={null} />
          <Route path="/monitor" element={<MonitorPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
      <StatusBar backendOnline={backendOnline} session={session} owned={ports.filter((p) => p.owned).map((p) => p.id)} onConnect={openConnect} />
      {connectOpen && <ConnectDialog initialHost={serverHint ?? '127.0.0.1'} initialPorts={serverPorts} initialParams={session.params} onClose={() => setConnectOpen(false)} />}
    </div>
  )
}
