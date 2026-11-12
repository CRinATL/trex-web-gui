import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { PortView } from '../api'
import { READINESS_HIDDEN_KEY, resetReadinessStore } from '../readiness'
import { wireProfiles } from '../testWire'
import { ReadinessStrip } from './ReadinessStrip'

const port = (o: Partial<PortView> = {}): PortView => ({
  id: 0, state: 'IDLE', owner: '', owned: false, service: false, service_filtered: false, link_up: true, speed_gbps: 10,
  promiscuous: false, multicast: false, fc_mode: 0, vlan: [], layer_cfg: {}, rx_filter_mode: null, profiles: wireProfiles(o.state), rx_info: {}, info: {}, ...o,
})
afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); resetReadinessStore() })

const current = () => document.querySelectorAll('[aria-current="step"]')

describe('ReadinessStrip', () => {
  it('renders the text in a plain paragraph, not a live region', () => {
    render(<ReadinessStrip port={port()} readOnly={false} />)
    const p = screen.getByText('Acquire the port, then assign a profile and start.')
    expect(p.tagName).toBe('P')
    expect(p).toHaveClass('readiness')
    expect(p).not.toHaveAttribute('aria-live')
    expect(p).not.toHaveAttribute('role')
    expect(document.querySelector('[aria-live], [role="status"], [role="alert"]')).toBeNull()
  })
  it.each([
    ['acquire', port(), false, 'Acquire'],
    ['assign', port({ owned: true, owner: 'me' }), false, 'Assign a profile'],
    ['start', port({ owned: true, state: 'STREAMS' }), false, 'Start'],
    ['running', port({ owned: true, state: 'TX' }), false, 'Start'],
  ])('step %s marks %s as current', (_s, p, ro, word) => {
    render(<ReadinessStrip port={p} readOnly={ro} />)
    expect(current()).toHaveLength(1)
    expect(current()[0]).toHaveTextContent(new RegExp(`^${word}$`))
  })
  it('blocked marks no word, and the trail and text still render', () => {
    render(<ReadinessStrip port={port({ owner: 'bob' })} readOnly={false} />)
    expect(current()).toHaveLength(0)
    expect(screen.getByText('Assign a profile')).toBeInTheDocument()
    expect(screen.getByText('Owned by bob: use Force acquire to take it.')).toBeInTheDocument()
  })
  it('Hide readiness strip writes the key and the strip disappears', async () => {
    render(<ReadinessStrip port={port()} readOnly={false} />)
    await userEvent.click(screen.getByRole('button', { name: 'Hide readiness strip' }))
    expect(localStorage.getItem(READINESS_HIDDEN_KEY)).toBe('1')
    expect(screen.queryByText('Acquire the port, then assign a profile and start.')).toBeNull()
  })
  it('with storage that throws, Hide still hides for the session', async () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied') })
    render(<ReadinessStrip port={port()} readOnly={false} />)
    await userEvent.click(screen.getByRole('button', { name: 'Hide readiness strip' }))
    expect(screen.queryByText('Acquire the port, then assign a profile and start.')).toBeNull()
  })
  it('renders nothing when the choice is hidden', () => {
    localStorage.setItem(READINESS_HIDDEN_KEY, '1'); resetReadinessStore()
    const { container } = render(<ReadinessStrip port={port()} readOnly={false} />)
    expect(container).toBeEmptyDOMElement()
  })
  // jsdom computes no layout, so read the stylesheet.
  const rule = (selector: string) => {
    const css = readFileSync(resolve(import.meta.dirname, '../index.css'), 'utf8')
    const m = [...css.matchAll(/(^|\n)([^{}\n]+)\{([^}]*)\}/g)].find((x) => x[2].trim() === selector)
    expect(m).toBeDefined()
    return m![3]
  }
  it('the strip wraps in a narrow window (read from index.css)', () => {
    expect(rule('.readiness-strip')).toMatch(/flex-wrap:\s*wrap/)
    expect(rule('.readiness-strip')).toMatch(/white-space:\s*normal/)
  })
  it('the trail arrow has an empty alternative text so a screen reader skips it (read from index.css)', () => {
    expect(rule('.readiness-trail li:not(:last-child)::after')).toMatch(/\/\s*''/)
  })
})
