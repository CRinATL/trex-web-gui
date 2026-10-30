import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Tunable } from '../api'
import { defaultsFor } from '../traffic'
import { TunablesForm } from './TunablesForm'

afterEach(cleanup)
const spec = (o: Partial<Tunable>): Tunable => ({ dest: 'x', flag: '--x', kind: 'int', default: null, choices: null, help: '', ...o })

function Harness({ specs, onValues }: { specs: Tunable[]; onValues: (v: Record<string, unknown>) => void }) {
  const [values, setValues] = useState(defaultsFor(specs))
  const [extra, setExtra] = useState('')
  return <TunablesForm specs={specs} values={values} extra={extra} onValues={(v) => { setValues(v); onValues(v) }} onExtra={setExtra} />
}

describe('TunablesForm', () => {
  it('store_false flags are checked when sent', async () => {
    const onValues = vi.fn()
    render(<Harness specs={[spec({ dest: 'vm', flag: '--no-vm', kind: 'bool_false', default: true })]} onValues={onValues} />)
    const box = screen.getByRole('checkbox', { name: '--no-vm' })
    expect(box).not.toBeChecked()                // the default (true) sends nothing
    await userEvent.click(box)
    expect(onValues).toHaveBeenLastCalledWith({ vm: false })
    expect(box).toBeChecked()                    // false sends --no-vm
  })

  it('choice without its default offers (default)', () => {
    render(<Harness specs={[spec({ dest: 'm', flag: '--mode', kind: 'choice', default: null, choices: ['a', 'b'] })]} onValues={vi.fn()} />)
    expect(screen.getByRole('option', { name: '(default)' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: /--mode/ })).toHaveValue('')
    cleanup()
    render(<Harness specs={[spec({ dest: 'm', flag: '--mode', kind: 'choice', default: 'a', choices: ['a', 'b'] })]} onValues={vi.fn()} />)
    expect(screen.queryByRole('option', { name: '(default)' })).not.toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: /--mode/ })).toHaveValue('a')
  })
})
