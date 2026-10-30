import { BrowserRouter } from 'react-router-dom'
import { AppShell } from './App'
import { EventsPage } from './pages/EventsPage'
import { AppStateProvider } from './state'

// The pop-out Events window is its own tree: no AppStateProvider (it would open a second WebSocket and publish its own log),
// no router, no top bar or status bar. It shows only what the main window sends over the BroadcastChannel.
export function Root({ pathname }: { pathname: string }) {
  if (pathname === '/events/popout') return <main><EventsPage popout /></main>
  return <AppStateProvider><BrowserRouter><AppShell /></BrowserRouter></AppStateProvider>
}
