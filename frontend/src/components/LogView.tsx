import { useEffect, useRef, useState } from 'react'
import type { LogEntry } from '../state'

const pad = (n: number) => String(n).padStart(2, '0')
// The browser's local date and time of a backend timestamp (seconds), as `YYYY-MM-DD` and `HH:mm:ss`.
const day = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
const hms = (d: Date) => `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
// The UTC offset in effect at `d`, as `-04:00` (ISO 8601) or `−04:00` for the screen.
const offset = (d: Date, sign = '-') => {
  const m = -d.getTimezoneOffset()
  return `${m < 0 ? sign : '+'}${pad(Math.floor(Math.abs(m) / 60))}:${pad(Math.abs(m) % 60)}`
}
// The column header's zone: the short name where the browser has one (`EDT`), always with the offset.
// eslint-disable-next-line react-refresh/only-export-components
export function zoneText(d: Date = new Date()): string {
  const name = new Intl.DateTimeFormat([], { timeZoneName: 'short' }).formatToParts(d).find((x) => x.type === 'timeZoneName')?.value
  const utc = `UTC${offset(d, '−')}`
  return name && !/^(GMT|UTC)/.test(name) ? `${name}, ${utc}` : utc
}

// One entry as a line of text, for Copy and Save log…: `[KIND] <ISO 8601 local time with offset> message`, or
// `--- <time> message ---` for a MARK. The legacy `HH:mm:ss` (parity A2.9) is kept inside the full stamp, so a saved log
// reads the same anywhere and across midnight.
// eslint-disable-next-line react-refresh/only-export-components
export function formatEntry(e: LogEntry): string {
  const d = new Date(e.ts * 1000)
  const at = `${day(d)}T${hms(d)}${offset(d)}`
  return e.kind === 'MARK' ? `--- ${at} ${e.message} ---` : `[${e.kind}] ${at} ${e.message}`
}

function When({ ts }: { ts: number }) {
  const d = new Date(ts * 1000)
  return <td className="log-time"><span className="muted">{day(d)}</span> {hms(d)}</td>
}

// Kinds are stored in capitals with underscores; shown as `server event`.
const kindLabel = (k: LogEntry['kind']) => k.toLowerCase().replace('_', ' ')

// The event log as a table (Time, Kind, Message). The scroller follows new entries while it is within 4 px of the bottom;
// once the user scrolls up it stays put and a Jump to newest button appears. `summary` leads the status line.
export function LogView({ entries, summary }: { entries: LogEntry[]; summary?: string }) {
  const box = useRef<HTMLDivElement>(null)
  const pinned = useRef(true)
  const [following, setFollowing] = useState(true)
  useEffect(() => { if (pinned.current && box.current) box.current.scrollTop = box.current.scrollHeight }, [entries])
  const jump = () => {
    pinned.current = true; setFollowing(true)
    if (box.current) box.current.scrollTop = box.current.scrollHeight
  }
  const onScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const t = e.currentTarget
    pinned.current = t.scrollTop + t.clientHeight >= t.scrollHeight - 4
    setFollowing(pinned.current)
  }
  return (
    <div className="logview">
      <p className="log-status muted">
        {summary && <>{summary} · </>}
        {following ? 'Following new entries' : <button type="button" onClick={jump}>Jump to newest</button>}
      </p>
      <div ref={box} role="log" tabIndex={0} className="log-scroll" onScroll={onScroll}>
        <table aria-label="Event log" className="log-table">
          <thead><tr><th scope="col" className="log-time">Time ({zoneText()})</th><th scope="col" className="log-kind">Kind</th><th scope="col">Message</th></tr></thead>
          <tbody>
            {entries.length === 0 && <tr><td colSpan={3} className="muted">No log entries yet.</td></tr>}
            {entries.map((e, i) => e.kind === 'MARK'
              ? <tr key={i} className="log-mark"><When ts={e.ts} /><td className="log-kind">session</td><td className="log-msg">{e.message}</td></tr>
              : <tr key={i} className={`log-${e.kind}`}><When ts={e.ts} /><td className="log-kind">{kindLabel(e.kind)}</td><td className="log-msg">{e.message}</td></tr>)}
          </tbody>
        </table>
      </div>
    </div>
  )
}
