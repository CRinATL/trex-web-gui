import { fireEvent, render, screen, within } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { EditedTag, UnsavedChanges, unsavedText, shown } from './Edited'

describe('EditedTag', () => {
  it('renders a span with class edited-tag and the text edited', () => {
    render(<EditedTag />)
    const tag = screen.getByText('edited')
    expect(tag.tagName).toBe('SPAN')
    expect(tag).toHaveClass('edited-tag')
  })
  it('takes an id, so a control can name it in aria-describedby', () => {
    render(<><input aria-label="Rate" aria-describedby="t" /><EditedTag id="t" /></>)
    expect(screen.getByLabelText('Rate')).toHaveAccessibleDescription('edited')
  })
})

describe('unsavedText', () => {
  it('counts changes, singular and plural, and is empty for none', () => {
    expect(unsavedText(1)).toBe('1 unsaved change')
    expect(unsavedText(3)).toBe('3 unsaved changes')
    expect(unsavedText(0)).toBe('')
  })
})

describe('UnsavedChanges', () => {
  const changes = [{ field: 'Host', saved: 'trex-a', edited: 'trex-b' }, { field: 'Timeout (s)', saved: '3', edited: '10' }]
  it('renders nothing when nothing is edited', () => {
    const { container } = render(<UnsavedChanges changes={[]} />)
    expect(container).toBeEmptyDOMElement()
  })
  it('is a button named by the count, closed until clicked', () => {
    render(<UnsavedChanges changes={changes} />)
    const button = screen.getByRole('button', { name: '2 unsaved changes' })
    expect(button).toHaveClass('unsaved-count')
    expect(button).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('table', { name: 'Unsaved changes' })).toBeNull()
  })
  it('shows each changed field with its saved and edited value, and closes on a second click', () => {
    render(<UnsavedChanges changes={changes} />)
    const button = screen.getByRole('button', { name: '2 unsaved changes' })
    fireEvent.click(button)
    expect(button).toHaveAttribute('aria-expanded', 'true')
    const table = screen.getByRole('table', { name: 'Unsaved changes' })
    const rows = within(table).getAllByRole('row').map((r) => [...r.querySelectorAll('th, td')].map((c) => c.textContent))
    expect(rows).toEqual([['Field', 'Saved', 'Edited'], ['Host', 'trex-a', 'trex-b'], ['Timeout (s)', '3', '10']])
    fireEvent.click(button)
    expect(screen.queryByRole('table', { name: 'Unsaved changes' })).toBeNull()
  })
  it('closes on Escape without the Escape reaching an enclosing dialog', () => {
    let reached = false
    const onWindowKey = () => { reached = true }
    window.addEventListener('keydown', onWindowKey)
    render(<UnsavedChanges changes={changes} />)
    const button = screen.getByRole('button', { name: '2 unsaved changes' })
    fireEvent.click(button)
    fireEvent.keyDown(button, { key: 'Escape' })
    window.removeEventListener('keydown', onWindowKey)
    expect(button).toHaveAttribute('aria-expanded', 'false')
    expect(reached).toBe(false)
  })
})

describe('shown', () => {
  it('names an empty value and cuts a long one', () => {
    expect(shown('')).toBe('(empty)')
    expect(shown('x'.repeat(100))).toBe(`${'x'.repeat(79)}…`)
    expect(shown(' a ')).toBe(' a ')
  })
  it('counts and cuts by code point, never inside a surrogate pair', () => {
    expect(shown('😀'.repeat(80))).toBe('😀'.repeat(80))
    expect(shown('😀'.repeat(81))).toBe(`${'😀'.repeat(79)}…`)
  })
})

describe('UnsavedChanges cells', () => {
  it('a cut value carries its whole text as the title; an empty or short one has none', () => {
    const long = 'y'.repeat(90)
    render(<UnsavedChanges changes={[{ field: 'Path', saved: '', edited: long }, { field: 'Name', saved: 'a', edited: 'b' }]} />)
    fireEvent.click(screen.getByRole('button', { name: '2 unsaved changes' }))
    const cells = within(screen.getByRole('table', { name: 'Unsaved changes' })).getAllByRole('cell')
    expect(cells.map((c) => c.getAttribute('title'))).toEqual([null, long, null, null])
    expect(cells[0]).toHaveTextContent('(empty)')
  })
})


describe('change list layout', () => {
  // The cells break anywhere, so a long value cannot widen the panel; that also let the table squeeze the column
  // headers to a letter's width ("Save|d", 1.1.1 re-shoot, shot 116). jsdom has no layout, so the rule is pinned.
  it('keeps the column headers on one line', () => {
    const css = readFileSync(resolve(import.meta.dirname, '../index.css'), 'utf8')
    expect(css).toMatch(/\.unsaved-list thead th \{[^}]*white-space: nowrap;[^}]*overflow-wrap: normal;/)
  })
})
