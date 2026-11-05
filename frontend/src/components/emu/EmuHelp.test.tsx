import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { EmuHelp } from './EmuHelp'

describe('EmuHelp', () => {
  it('says how to start EMU, what the ports need (R0.5) and that there is no ownership', async () => {
    render(<EmuHelp />)
    await userEvent.click(screen.getByRole('button', { name: 'About EMU' }))
    const note = document.body.textContent ?? ''
    expect(note).toContain('./t-rex-64 -i --emu')
    expect(note).toContain("EMU answers through a TRex port in any service state on the AF_PACKET test guest; on hardware NICs use service filtered mode with mask 29 (the console's service --emu)")
    expect(note).toContain('EMU has no ownership')
  })
})
