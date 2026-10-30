export interface SocketDeps {
  // GET /api/auth/status: false when the browser's cookie is not accepted (never given the URL, or the backend restarted)
  authStatus: () => Promise<boolean>
  onUnauthenticated: () => void
}

export function connectSocket(url: string, onMessage: (m: unknown) => void, onStatus: (open: boolean) => void, deps: SocketDeps): () => void {
  let delay = 500
  let stopped = false
  let timer: ReturnType<typeof setTimeout> | undefined
  let sock: WebSocket | undefined
  const schedule = () => {
    timer = setTimeout(open, delay)
    delay = Math.min(delay * 2, 5000)
  }
  const open = () => {
    const s = new WebSocket(url)
    sock = s
    let opened = false      // per connection attempt
    s.onopen = () => { opened = true; onStatus(true) }
    s.onmessage = (e: MessageEvent) => {
      let msg: unknown
      try { msg = JSON.parse(e.data as string) } catch { console.warn('ws: ignoring malformed frame'); return }
      delay = 500 // reset on the first received message: a server that accepts and then closes must not cause a tight loop
      onMessage(msg)
    }
    s.onclose = () => {
      if (stopped) return
      onStatus(false)
      if (opened) { schedule(); return }
      // Closed before it opened: a refused handshake is close code 1006 in a browser (the server's 1008 never reaches
      // it), the same as a backend that is down. Ask the status route which one it is.
      deps.authStatus().then(
        (ok) => {
          if (stopped) return      // stopped while the status call was in flight
          if (ok) schedule(); else { stopped = true; deps.onUnauthenticated() }
        },
        () => { if (!stopped) schedule() },      // the status call failed too: the backend is down, keep trying
      )
    }
  }
  open()
  return () => { stopped = true; if (timer) clearTimeout(timer); sock?.close() }
}
