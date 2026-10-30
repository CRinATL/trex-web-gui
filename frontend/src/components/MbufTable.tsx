import { useSyncExternalStore } from 'react'
import { cell } from '../statsFormat'
import { utilStore } from '../utilStats'
import { mbufBytes, socketName } from '../utilTables'

type Pair = { free: number | null; total: number | null }

const num = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

// to_table_mbuf's RAM column: int(sum(count * bytes + 64) / 1e6), over the sizes. null when a count is missing.
function ram(sizes: string[], count: (size: string) => number | null, sockets: number): string {
  let sum = 0
  for (const s of sizes) {
    const n = count(s)
    if (n === null) return '–'
    sum += n * mbufBytes(s) + 64
  }
  return cell(Math.trunc((sum * sockets) / 1e6))
}

// The mbuf table of the Utilization tab, with to_table_mbuf's (the tui source) formulas: Total RAM is the first socket's
// totals times the socket count; per socket, used = total - free, RAM adds 64 per size, percent is int(100 * used / total).
export function MbufTable() {
  useSyncExternalStore(utilStore.subscribe, utilStore.version)
  const mbuf = utilStore.latest()?.mbuf
  const sockets = mbuf ? Object.keys(mbuf).sort() : []
  if (!mbuf || sockets.length === 0) return <p>No utilization data yet.</p>
  const first = Object.keys(mbuf)[0]
  const sizes = Object.keys(mbuf[first]).sort((a, b) => mbufBytes(a) - mbufBytes(b))
  const pair = (sock: string, size: string): Pair | undefined => mbuf[sock]?.[size]
  const used = (sock: string, size: string): number | null => {
    const p = pair(sock, size)
    return p && num(p.total) && num(p.free) ? p.total - p.free : null
  }
  const pct = (sock: string, size: string): string => {
    const u = used(sock, size)
    const t = pair(sock, size)?.total
    return u === null || !num(t) || t === 0 ? '–' : `${Math.trunc((100 * u) / t)}%`
  }
  const total = (size: string) => { const t = pair(first, size)?.total; return num(t) ? t : null }
  return (
    <table className="dash-table" aria-label="Mbuf utilization">
      <thead>
        <tr><th scope="col" />{sizes.map((s) => <th key={s} scope="col">{s}</th>)}<th scope="col">RAM (MB)</th></tr>
      </thead>
      <tbody>
        <tr>
          <th scope="row">Total</th>
          {sizes.map((s) => <td key={s}>{cell(total(s))}</td>)}
          <td>{ram(sizes, total, sockets.length)}</td>
        </tr>
        {sockets.map((sock) => [
          <tr key={`${sock}-used`}>
            <th scope="row">{socketName(sock)} used</th>
            {sizes.map((s) => <td key={s}>{cell(used(sock, s))}</td>)}
            <td>{ram(sizes, (s) => used(sock, s), 1)}</td>
          </tr>,
          <tr key={`${sock}-pct`}>
            <th scope="row">{socketName(sock)} percent</th>
            {sizes.map((s) => <td key={s}>{pct(sock, s)}</td>)}
            <td />
          </tr>,
        ])}
      </tbody>
    </table>
  )
}
