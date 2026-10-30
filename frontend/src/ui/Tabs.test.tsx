import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Tabs, tabPanel } from './Tabs'

type Id = 'a' | 'b' | 'c'
const TABS: { id: Id; label: string }[] = [{ id: 'a', label: 'Alpha' }, { id: 'b', label: 'Beta' }, { id: 'c', label: 'Gamma' }]

function Harness({ orientation }: { orientation?: 'horizontal' | 'vertical' }) {
  const [sel, setSel] = useState<Id>('a')
  return (
    <div>
      <Tabs label="Demo" idPrefix="demo" tabs={TABS} selected={sel} onSelect={setSel} orientation={orientation} end={<button type="button">Extra</button>} />
      <div {...tabPanel('demo', 'a', sel)}>panel a</div>
      <div {...tabPanel('demo', 'b', sel)}>panel b</div>
    </div>
  )
}

const tab = (name: string) => screen.getByRole('tab', { name })

describe('Tabs', () => {
  it('arrow keys, Home and End move selection and focus', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    tab('Alpha').focus()
    await user.keyboard('{ArrowRight}')
    expect(tab('Beta')).toHaveAttribute('aria-selected', 'true'); expect(tab('Beta')).toHaveFocus()
    await user.keyboard('{End}')
    expect(tab('Gamma')).toHaveAttribute('aria-selected', 'true'); expect(tab('Gamma')).toHaveFocus()
    await user.keyboard('{Home}')
    expect(tab('Alpha')).toHaveAttribute('aria-selected', 'true'); expect(tab('Alpha')).toHaveFocus()
    await user.keyboard('{ArrowLeft}')
    expect(tab('Gamma')).toHaveAttribute('aria-selected', 'true'); expect(tab('Gamma')).toHaveFocus()
    expect(tab('Gamma')).toHaveAttribute('tabindex', '0'); expect(tab('Alpha')).toHaveAttribute('tabindex', '-1')
  })

  it('vertical tabs use ArrowDown and ArrowUp', async () => {
    const user = userEvent.setup()
    render(<Harness orientation="vertical" />)
    expect(screen.getByRole('tablist')).toHaveAttribute('aria-orientation', 'vertical')
    tab('Alpha').focus()
    await user.keyboard('{ArrowRight}')
    expect(tab('Alpha')).toHaveAttribute('aria-selected', 'true')
    await user.keyboard('{ArrowDown}')
    expect(tab('Beta')).toHaveAttribute('aria-selected', 'true'); expect(tab('Beta')).toHaveFocus()
    await user.keyboard('{ArrowUp}{ArrowUp}')
    expect(tab('Gamma')).toHaveAttribute('aria-selected', 'true')
  })

  it('aria-controls points at the panel tabPanel renders', () => {
    render(<Harness />)
    const controls = tab('Alpha').getAttribute('aria-controls')!
    const panel = document.getElementById(controls)!
    expect(panel).toHaveAttribute('role', 'tabpanel')
    expect(panel).toHaveAttribute('aria-labelledby', tab('Alpha').id)
    expect(tab('Alpha').id).toBe('demo-tab-a')
    expect(document.getElementById('demo-panel-b')).toHaveAttribute('hidden')
  })

  it('end content renders after the tabs', () => {
    render(<Harness />)
    const extra = screen.getByRole('button', { name: 'Extra' })
    expect(tab('Gamma').compareDocumentPosition(extra) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('end content is outside the tablist', () => {
    render(<Harness />)
    expect(screen.getByRole('tablist')).not.toContainElement(screen.getByRole('button', { name: 'Extra' }))
  })

  it('renders no tabs-end element without end content', () => {
    const { container } = render(<Tabs label="Demo" idPrefix="demo" tabs={TABS} selected="a" onSelect={() => {}} />)
    expect(container.querySelector('.tabs-end')).toBeNull()
  })
})
