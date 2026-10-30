import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { useDialogFocus } from './useDialogFocus'

function Dlg() {
  const ref = useDialogFocus<HTMLDivElement>('button')
  return <div ref={ref} role="dialog"><button>First</button><button>Last</button></div>
}

// A roving-tabindex widget (the hex grid) leaves tabIndex -1 on its other cells, here before and after the tab stops.
function Roving() {
  const ref = useDialogFocus<HTMLDivElement>('.first')
  return (
    <div ref={ref} role="dialog">
      <button tabIndex={-1}>Cell A</button><button className="first">First</button><button>Last</button><button tabIndex={-1}>Cell B</button>
    </div>
  )
}

describe('useDialogFocus trap', () => {
  it('wraps Tab from the last control to the first and Shift+Tab back', async () => {
    render(<><button>Outside</button><Dlg /></>)
    expect(screen.getByRole('button', { name: 'First' })).toHaveFocus()
    await userEvent.tab()
    expect(screen.getByRole('button', { name: 'Last' })).toHaveFocus()
    await userEvent.tab()
    expect(screen.getByRole('button', { name: 'First' })).toHaveFocus()
    await userEvent.tab({ shift: true })
    expect(screen.getByRole('button', { name: 'Last' })).toHaveFocus()
  })
  it('skips elements whose tabIndex is below 0 at either end, so Tab and Shift+Tab never leave the dialog', async () => {
    render(<><button>Outside</button><Roving /></>)
    expect(screen.getByRole('button', { name: 'First' })).toHaveFocus()
    await userEvent.tab({ shift: true })
    expect(screen.getByRole('button', { name: 'Last' })).toHaveFocus()
    await userEvent.tab()
    expect(screen.getByRole('button', { name: 'First' })).toHaveFocus()
  })
})
