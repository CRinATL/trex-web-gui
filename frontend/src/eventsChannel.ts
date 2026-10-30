import { useEffect, useState } from 'react'
import type { LogEntry } from './state'

// The main window sends its event log to a pop-out Events window over this BroadcastChannel; the pop-out has no WebSocket of its own.
const NAME = 'trex-web-gui-events'
type Msg = { type: 'hello' } | { type: 'log'; entries: LogEntry[] }

const supported = () => typeof BroadcastChannel === 'function'

// The main window's channel and the log it last published, so a pop-out that says hello later gets the current log.
let main: { ctor: typeof BroadcastChannel; channel: BroadcastChannel } | null = null
let latest: LogEntry[] = []

// Posts the log to any pop-out and answers a pop-out's hello with it. A no-op without BroadcastChannel.
export function publishLog(entries: LogEntry[]): void {
  if (!supported()) return
  latest = entries
  if (!main || main.ctor !== BroadcastChannel) {
    main?.channel.close()
    const channel = new BroadcastChannel(NAME)
    channel.onmessage = (e: MessageEvent<Msg>) => { if (e.data?.type === 'hello') channel.postMessage({ type: 'log', entries: latest } satisfies Msg) }
    main = { ctor: BroadcastChannel, channel }
  }
  main.channel.postMessage({ type: 'log', entries } satisfies Msg)
}

// The pop-out's view of the log: null until the main window has sent it. Says hello on mount so a main window that published earlier answers.
export function useChannelLog(): LogEntry[] | null {
  const [entries, setEntries] = useState<LogEntry[] | null>(null)
  useEffect(() => {
    if (!supported()) return
    const channel = new BroadcastChannel(NAME)
    channel.onmessage = (e: MessageEvent<Msg>) => { if (e.data?.type === 'log') setEntries(e.data.entries) }
    channel.postMessage({ type: 'hello' } satisfies Msg)
    return () => channel.close()
  }, [])
  return entries
}
