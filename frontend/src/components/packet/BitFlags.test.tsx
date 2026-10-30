import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { FieldDef } from '../../api'
import defsFixture from '../../__fixtures__/packet/definitions.json'
import { BitFlags } from './BitFlags'

afterEach(cleanup)
const defs = (defsFixture as unknown as { definitions: { protocols: { id: string; fields: FieldDef[] }[] } }).definitions
const tcpFlags = defs.protocols.find((p) => p.id === 'TCP')!.fields.find((f) => f.id === 'flags')!

describe('BitFlags', () => {
  it('TCP flags SYN toggles mask 2', async () => {
    const onCommit = vi.fn()
    const { rerender } = render(<BitFlags def={tcpFlags} value={0x10} onCommit={onCommit} />)
    expect(screen.getByRole('checkbox', { name: 'ACK' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'SYN' })).not.toBeChecked()
    await userEvent.click(screen.getByRole('checkbox', { name: 'SYN' }))
    expect(onCommit).toHaveBeenLastCalledWith(0x12)   // ACK stays, SYN (mask 2) is set
    rerender(<BitFlags def={tcpFlags} value={0x12} onCommit={onCommit} />)
    await userEvent.click(screen.getByRole('checkbox', { name: 'SYN' }))
    expect(onCommit).toHaveBeenLastCalledWith(0x10)
  })

  it('a group with several values is a choice', async () => {
    const onCommit = vi.fn()
    const def: FieldDef = { id: 'x', name: 'X', type: 'BITMASK', bits: [{ mask: 0x0c, name: 'Mode', values: [{ name: 'A', value: 0 }, { name: 'B', value: 4 }, { name: 'C', value: 8 }] }] }
    render(<BitFlags def={def} value={0x41} onCommit={onCommit} />)
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Mode' }), 'C')
    expect(onCommit).toHaveBeenCalledWith(0x49)
  })
})
