import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { EditedTag, unsavedText } from './Edited'

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
