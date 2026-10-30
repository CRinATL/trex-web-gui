import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { LogEntry } from '../state'
import { formatEntry, LogView, zoneText } from './LogView'

const time = (ts: number) => new Date(ts * 1000).toLocaleTimeString([], { hour12: false })
// The local ISO 8601 stamp with offset, built independently of LogView's helpers.
const iso = (ts: number) => {
  const d = new Date(ts * 1000), p = (n: number) => String(n).padStart(2, '0'), m = -d.getTimezoneOffset()
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
    + `${m < 0 ? '-' : '+'}${p(Math.floor(Math.abs(m) / 60))}:${p(Math.abs(m) % 60)}`
}

// jsdom has no layout: give the scroller a geometry so the follow rule has something to measure.
const geom = (el: HTMLElement, top: number) => {
  Object.defineProperty(el, 'scrollHeight', { configurable: true, value: 1000 })
  Object.defineProperty(el, 'clientHeight', { configurable: true, value: 100 })
  el.scrollTop = top
}

describe('LogView', () => {
  it('formats entries as [TYPE] <local date and time with UTC offset> message, the legacy HH:mm:ss inside it', () => {
    expect(formatEntry({ kind: 'ERROR', ts: 43200, message: 'boom' })).toMatch(/^\[ERROR\] \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2} boom$/)
    expect(formatEntry({ kind: 'ERROR', ts: 43200, message: 'boom' })).toBe(`[ERROR] ${iso(43200)} boom`)
    expect(formatEntry({ kind: 'ERROR', ts: 43200, message: 'boom' })).toContain(time(43200))
    expect(formatEntry({ kind: 'MARK', ts: 43200, message: 'Disconnected' })).toBe(`--- ${iso(43200)} Disconnected ---`)
    // the stamp is the same instant: parsing it back gives the timestamp
    expect(Date.parse(iso(43200)) / 1000).toBe(43200)
  })
  it('the Time header names the browser\'s zone with its UTC offset', () => {
    render(<LogView entries={[]} />)
    expect(screen.getByRole('columnheader', { name: /^Time \((\S+, )?UTC[+−]\d{2}:\d{2}\)$/ })).toBeInTheDocument()
    expect(zoneText()).toMatch(/UTC[+−]\d{2}:\d{2}$/)
  })
  it('shows time, lower-case kind and message in an Event log table', () => {
    render(<LogView entries={[{ kind: 'SERVER_EVENT', ts: 43200, message: 'boom' }]} />)
    const table = screen.getByRole('table', { name: 'Event log' })
    expect(table).toHaveTextContent('server event')
    expect(table).toHaveTextContent(time(43200))
    expect(screen.getByRole('columnheader', { name: 'Kind' })).toBeInTheDocument()
    expect(screen.getByText('boom')).toBeInTheDocument()
  })
  it('a MARK row lines up with the columns: its time, `session`, then the message', () => {
    render(<LogView entries={[{ kind: 'MARK', ts: 43200, message: 'Connection lost: gone' }]} />)
    const row = screen.getByText('Connection lost: gone').closest('tr')!
    expect(row).toHaveClass('log-mark')
    expect([...row.cells].map((c) => c.textContent)).toEqual([`${iso(43200).slice(0, 10)} ${time(43200)}`, 'session', 'Connection lost: gone'])
  })
  it('follows new entries until the user scrolls up', () => {
    const e1: LogEntry[] = [{ kind: 'INFO', ts: 1, message: 'a' }]
    const e2: LogEntry[] = [...e1, { kind: 'INFO', ts: 2, message: 'b' }]
    const e3: LogEntry[] = [...e2, { kind: 'INFO', ts: 3, message: 'c' }]
    const { rerender } = render(<LogView entries={e1} />)
    const box = screen.getByRole('log')
    geom(box, 0)
    rerender(<LogView entries={e2} />)
    expect(box.scrollTop).toBe(1000) // pinned: followed
    geom(box, 200)
    box.dispatchEvent(new Event('scroll', { bubbles: true })) // user scrolled up
    rerender(<LogView entries={e3} />)
    expect(box.scrollTop).toBe(200)
  })
  it('shows Jump to newest once scrolled up, and it scrolls back', async () => {
    render(<LogView entries={[{ kind: 'INFO', ts: 1, message: 'a' }]} />)
    const box = screen.getByRole('log')
    expect(screen.getByText('Following new entries')).toBeInTheDocument()
    geom(box, 200)
    act(() => { fireEvent.scroll(box) })
    expect(screen.queryByText('Following new entries')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Jump to newest' }))
    expect(box.scrollTop).toBe(1000)
    expect(screen.getByText('Following new entries')).toBeInTheDocument()
  })
})
