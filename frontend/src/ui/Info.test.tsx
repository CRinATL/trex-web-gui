import { describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useHotkey } from '../keys'
import { Info } from './Info'

describe('Info', () => {
  it('the text is in the DOM but hidden until the button is pressed', async () => {
    const user = userEvent.setup()
    render(<Info label="Speed">Link speed note</Info>)
    expect(screen.getByText('Link speed note')).not.toBeVisible()
    const btn = screen.getByRole('button', { name: 'About Speed' })
    expect(btn).toHaveAttribute('aria-expanded', 'false')
    await user.click(btn)
    expect(screen.getByText('Link speed note')).toBeVisible()
    expect(btn).toHaveAttribute('aria-expanded', 'true')
  })

  it('Escape closes it', async () => {
    const user = userEvent.setup()
    render(<Info label="Speed">Link speed note</Info>)
    await user.click(screen.getByRole('button', { name: 'About Speed' }))
    await user.keyboard('{Escape}')
    expect(screen.getByText('Link speed note')).not.toBeVisible()
    expect(screen.getByRole('button', { name: 'About Speed' })).toHaveAttribute('aria-expanded', 'false')
  })

  it('Escape on an open ⓘ inside a dialog closes the ⓘ and leaves the dialog open', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    function Dlg() {
      useHotkey('escape', onClose)
      return <div role="dialog" aria-label="Demo"><Info label="Speed">Link speed note</Info></div>
    }
    render(<Dlg />)
    await user.click(screen.getByRole('button', { name: 'About Speed' }))
    await user.keyboard('{Escape}')
    expect(screen.getByText('Link speed note')).not.toBeVisible()
    expect(onClose).not.toHaveBeenCalled()
    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('opening does not change the parent row', async () => {
    const user = userEvent.setup()
    const { container } = render(<div data-testid="row"><Info label="Speed">Link speed note</Info></div>)
    const row = container.firstElementChild as HTMLElement
    const before = row.querySelectorAll('*').length
    await user.click(screen.getByRole('button', { name: 'About Speed' }))
    expect(row.querySelectorAll('*').length).toBe(before)
    expect(row.contains(screen.getByText('Link speed note'))).toBe(false)
  })

  it('note inside a modal is portalled', async () => {
    const user = userEvent.setup()
    render(<div className="modal"><Info label="Speed">Link speed note</Info></div>)
    await user.click(screen.getByRole('button', { name: 'About Speed' }))
    const note = screen.getByText('Link speed note')
    expect(note.parentElement).toBe(document.body)
    expect(note.closest('.modal')).toBeNull()
    expect(note).toBeVisible()
  })

  it('an outside pointerdown closes it, a pointerdown inside the note does not, a second click closes it', async () => {
    const user = userEvent.setup()
    render(<div><Info label="Speed">Link speed note</Info><p>elsewhere</p></div>)
    const btn = screen.getByRole('button', { name: 'About Speed' })
    await user.click(btn)
    await user.pointer({ keys: '[MouseLeft>]', target: screen.getByText('Link speed note') })
    expect(btn).toHaveAttribute('aria-expanded', 'true')
    await user.click(screen.getByText('elsewhere'))
    expect(btn).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByText('Link speed note')).not.toBeVisible()
    await user.click(btn)
    expect(btn).toHaveAttribute('aria-expanded', 'true')
    await user.click(btn)
    expect(btn).toHaveAttribute('aria-expanded', 'false')
  })

  it('the button points at its note', async () => {
    render(<Info label="Speed">Link speed note</Info>)
    const btn = screen.getByRole('button', { name: 'About Speed' })
    expect(btn.getAttribute('aria-controls')).toBeTruthy()
    expect(document.getElementById(btn.getAttribute('aria-controls')!)).toBe(screen.getByText('Link speed note'))
  })

  it.each([['a window resize', () => fireEvent(window, new Event('resize'))],
    ['a scroll of a scrolling ancestor', () => fireEvent.scroll(screen.getByTestId('pane'))],
    ['a scroll of the page', () => fireEvent.scroll(document)]])('%s closes it', async (_n, move) => {
    const user = userEvent.setup()
    render(<div data-testid="pane" className="modal"><Info label="Speed">Link speed note</Info></div>)
    const btn = screen.getByRole('button', { name: 'About Speed' })
    await user.click(btn)
    expect(btn).toHaveAttribute('aria-expanded', 'true')
    move()
    expect(btn).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByText('Link speed note')).not.toBeVisible()
  })

  it('a scroll of an unrelated pane leaves it open', async () => {
    const user = userEvent.setup()
    render(<div><div data-testid="log" /><Info label="Speed">Link speed note</Info></div>)
    const btn = screen.getByRole('button', { name: 'About Speed' })
    await user.click(btn)
    fireEvent.scroll(screen.getByTestId('log'))
    expect(btn).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Link speed note')).toBeVisible()
  })

  // jsdom has no IntersectionObserver: a fake one records what is observed and lets the test report a visibility change.
  function fakeObserver() {
    const made: { cb: IntersectionObserverCallback; seen: Element[]; disconnected: boolean }[] = []
    class FakeIO {
      rec: (typeof made)[number]
      constructor(cb: IntersectionObserverCallback) { this.rec = { cb, seen: [], disconnected: false }; made.push(this.rec) }
      observe(el: Element) { this.rec.seen.push(el) }
      unobserve() {}
      disconnect() { this.rec.disconnected = true }
      takeRecords() { return [] }
    }
    vi.stubGlobal('IntersectionObserver', FakeIO)
    const report = (visible: boolean) => act(() => {
      for (const r of made) if (!r.disconnected) r.cb(r.seen.map((target) => ({ target, isIntersecting: visible }) as IntersectionObserverEntry), {} as IntersectionObserver)
    })
    return { made, report }
  }

  it('closes when its button is hidden without a pointer event (a page or tab switch)', async () => {
    const io = fakeObserver()
    try {
      const user = userEvent.setup()
      render(<Info label="Speed">Link speed note</Info>)
      const btn = screen.getByRole('button', { name: 'About Speed' })
      await user.click(btn)
      expect(io.made.at(-1)?.seen).toEqual([btn])
      io.report(true)    // the first report, while the button shows: stays open
      expect(screen.getByText('Link speed note')).toBeVisible()
      io.report(false)
      expect(btn).toHaveAttribute('aria-expanded', 'false')
      expect(screen.getByText('Link speed note')).not.toBeVisible()
      expect(io.made.every((r) => r.disconnected)).toBe(true)   // closed: nothing left observing
    } finally { vi.unstubAllGlobals() }
  })

  it('opens and closes where IntersectionObserver is missing', async () => {
    vi.stubGlobal('IntersectionObserver', undefined)
    try {
      const user = userEvent.setup()
      render(<Info label="Speed">Link speed note</Info>)
      const btn = screen.getByRole('button', { name: 'About Speed' })
      await user.click(btn)
      expect(screen.getByText('Link speed note')).toBeVisible()
      await user.click(btn)
      expect(screen.getByText('Link speed note')).not.toBeVisible()
    } finally { vi.unstubAllGlobals() }
  })

  it('an open note goes with its button on unmount', async () => {
    const user = userEvent.setup()
    const { unmount } = render(<Info label="Speed">Link speed note</Info>)
    await user.click(screen.getByRole('button', { name: 'About Speed' }))
    unmount()
    expect(screen.queryByText('Link speed note')).toBeNull()
  })
})
