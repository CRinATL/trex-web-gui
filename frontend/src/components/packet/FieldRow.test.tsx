import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { FieldDef } from '../../api'
import { FieldRow } from './FieldRow'
import type { CombinedField } from './packetModel'

afterEach(cleanup)

const field = (def: FieldDef, over: Partial<CombinedField> = {}): CombinedField => ({
  id: def.id, def, value: 64, shown: '64', userSet: false, ignored: false, readOnly: false, range: [22, 23], bits: null, error: null, ...over,
})
const TTL: FieldDef = { id: 'ttl', name: 'TTL', type: 'NUMBER', min: 1, max: 255 }
// The names are a host's tables (R7): this one has no LOOP and no 0x9000 name.
const ETYPE: FieldDef = { id: 'type', name: 'Type', type: 'ENUM', values_dict: { ARP: 2054, IPv4: 2048, IPv6: 34525 } }
const MAC: FieldDef = { id: 'dst', name: 'Destination', type: 'MAC_ADDRESS', regex: '^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$' }

function setup(f: CombinedField, extra: Partial<React.ComponentProps<typeof FieldRow>> = {}) {
  const onCommit = vi.fn(), onReset = vi.fn()
  render(<FieldRow field={f} onCommit={onCommit} onReset={onReset} {...extra} />)
  return { onCommit, onReset }
}

describe('FieldRow', () => {
  it('should_set_field_text_with_enter', async () => {
    const { onCommit } = setup(field(MAC, { value: '00:00:00:00:00:00', shown: '00:00:00:00:00:00' }))
    const box = screen.getByRole('textbox', { name: 'Destination' })
    await userEvent.clear(box)
    await userEvent.type(box, '52:52:18:89:91:ea{Enter}')
    expect(onCommit).toHaveBeenCalledTimes(1)
    expect(onCommit).toHaveBeenCalledWith('52:52:18:89:91:ea')
  })

  it('should_cancel_field_editing_with_ESC', async () => {
    const { onCommit } = setup(field(TTL))
    const box = screen.getByRole('textbox', { name: 'TTL' })
    await userEvent.clear(box)
    await userEvent.type(box, '12{Escape}')
    expect(box).toHaveValue('64')
    await userEvent.tab()
    expect(onCommit).not.toHaveBeenCalled()
  })

  it('an Enter commit drops the draft, so an undo with the input still focused shows the document\'s value', async () => {
    const onCommit = vi.fn()
    const row = (shown: string) => <FieldRow field={field(TTL, { shown, value: Number(shown) })} onCommit={onCommit} onReset={() => undefined} />
    const { rerender } = render(row('64'))
    const box = screen.getByRole('textbox', { name: 'TTL' })
    await userEvent.clear(box)
    await userEvent.type(box, '12{Enter}')
    expect(onCommit).toHaveBeenCalledWith(12)
    rerender(row('12'))                       // the document took the value
    expect(box).toHaveValue('12')
    rerender(row('64'))                       // Ctrl+Z in the editor: the document is back at 64, the input still has focus
    expect(box).toHaveFocus()
    expect(box).toHaveValue('64')             // not the stale '12' draft
  })

  it.each([['z', false], ['y', false], ['z', true], ['r', false]])('Ctrl+%s (shift %s) while text is being typed reverts the text and does not reach the editor', async (key, shift) => {
    const reached = vi.fn()
    const onCommit = vi.fn()
    render(<div onKeyDown={reached}><FieldRow field={field(TTL)} onCommit={onCommit} onReset={() => undefined} /></div>)
    const box = screen.getByRole('textbox', { name: 'TTL' })
    await userEvent.clear(box)
    await userEvent.type(box, '12')
    reached.mockClear()
    await userEvent.keyboard(`{Control>}${shift ? '{Shift>}' : ''}${key}${shift ? '{/Shift}' : ''}{/Control}`)
    expect(box).toHaveValue('64')
    expect(reached.mock.calls.filter(([e]) => e.key.toLowerCase() === key)).toHaveLength(0)
    expect(onCommit).not.toHaveBeenCalled()
    await userEvent.keyboard('{Control>}z{/Control}')     // the text is clean again: the key goes on to the editor
    expect(reached.mock.calls.filter(([e]) => e.key.toLowerCase() === 'z')).toHaveLength(1)
  })

  it('should_not_set_incorrect_field_value', async () => {
    const { onCommit } = setup(field(MAC, { value: '00:00:00:00:00:00', shown: '00:00:00:00:00:00' }))
    const box = screen.getByRole('textbox', { name: 'Destination' })
    await userEvent.clear(box)
    await userEvent.type(box, 'not a mac{Enter}')
    expect(onCommit).not.toHaveBeenCalled()
    expect(box).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByRole('alert')).toHaveTextContent('not a valid value')
    expect(box).toHaveValue('not a mac')
  })

  it('should_select_all_field_content_on_field_click', async () => {
    setup(field(TTL, { shown: '1234' }))
    const box = screen.getByRole('textbox', { name: 'TTL' }) as HTMLInputElement
    await userEvent.click(box)
    expect([box.selectionStart, box.selectionEnd]).toEqual([0, 4])
  })

  it('should_save_enumfield_values', async () => {
    const { onCommit } = setup(field(ETYPE, { value: 2048, shown: 'IPv4' }))
    const box = screen.getByRole('combobox', { name: 'Type' })
    await userEvent.clear(box)
    await userEvent.type(box, 'IPv6{Enter}')
    expect(onCommit).toHaveBeenCalledWith(34525)   // the number, never the name (R13)
  })

  it('choosing an enum name commits its number', () => {
    const { onCommit } = setup(field(ETYPE, { value: 2048, shown: 'IPv4' }))
    // a choice from the list arrives as one change event, not as keystrokes
    fireEvent.change(screen.getByRole('combobox', { name: 'Type' }), { target: { value: 'ARP' } })
    expect(onCommit).toHaveBeenCalledTimes(1)
    expect(onCommit).toHaveBeenCalledWith(2054)
    expect(typeof onCommit.mock.calls[0][0]).toBe('number')
  })

  it('the list holds the host\'s names and no others', () => {
    const { container } = render(<FieldRow field={field(ETYPE)} onCommit={() => undefined} onReset={() => undefined} />)
    expect([...container.querySelectorAll('datalist option')].map((o) => o.getAttribute('value'))).toEqual(['ARP', 'IPv4', 'IPv6'])
  })

  it('a typed hex enum value commits', async () => {
    const { onCommit } = setup(field(ETYPE, { value: 2048, shown: 'IPv4' }))
    const box = screen.getByRole('combobox', { name: 'Type' })
    await userEvent.clear(box)
    await userEvent.type(box, '0x9000{Enter}')   // a number this host has no name for (R7)
    expect(onCommit).toHaveBeenCalledWith(0x9000)
  })

  it('an enum name this host lacks is not committed', async () => {
    const { onCommit } = setup(field(ETYPE, { value: 2048, shown: 'IPv4' }))
    const box = screen.getByRole('combobox', { name: 'Type' })
    await userEvent.clear(box)
    await userEvent.type(box, 'LOOP{Enter}')
    expect(onCommit).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('not a name or a number')
  })

  it('× resets to default', async () => {
    const { onReset } = setup(field(TTL, { userSet: true }))
    await userEvent.click(screen.getByRole('button', { name: 'Reset TTL to default' }))
    expect(onReset).toHaveBeenCalledTimes(1)
  })

  it('a field that is not user-set has no ×', () => {
    setup(field(TTL))
    expect(screen.queryByRole('button', { name: /Reset/ })).toBeNull()
  })

  it('a value outside min/max is not committed', async () => {
    const { onCommit } = setup(field(TTL))
    const box = screen.getByRole('textbox', { name: 'TTL' })
    for (const bad of ['0', '256']) {
      await userEvent.clear(box)
      await userEvent.type(box, `${bad}{Enter}`)
    }
    expect(onCommit).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('must be from 1 to 255')
    await userEvent.clear(box)
    await userEvent.type(box, '0x20{Enter}')
    expect(onCommit).toHaveBeenCalledWith(32)
  })

  it('a rejected value stays in the field, red, with the message', () => {
    // combine() hands the user's text as `shown` while the field is in error
    setup(field(TTL, { shown: 'abc', userSet: true, error: 'ValueError: bad ttl' }))
    const box = screen.getByRole('textbox', { name: 'TTL' })
    expect(box).toHaveValue('abc')
    expect(box).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByRole('alert')).toHaveTextContent('ValueError: bad ttl')
    expect(screen.getByRole('img', { name: 'error' })).toHaveTextContent('!')
    expect(box.closest('.field-row')).toHaveClass('error')
  })

  it('byte range text and bits marker', () => {
    const { rerender } = render(<FieldRow field={field(TTL, { range: [14, 15] })} onCommit={() => undefined} onReset={() => undefined} />)
    expect(screen.getByText('000e-000e [0001]')).toBeInTheDocument()
    rerender(<FieldRow field={field({ id: 'ihl', name: 'IHL', type: 'NUMBER' }, { range: null })} onCommit={() => undefined} onReset={() => undefined} />)
    expect(screen.getByText('bits')).toBeInTheDocument()
  })

  it('user-set shows the glyph, not only the colour', () => {
    const { rerender } = render(<FieldRow field={field(TTL, { userSet: true })} onCommit={() => undefined} onReset={() => undefined} />)
    expect(screen.getByRole('img', { name: 'user-set' })).toHaveTextContent('•')
    rerender(<FieldRow field={field(TTL)} onCommit={() => undefined} onReset={() => undefined} />)
    expect(screen.queryByRole('img', { name: 'user-set' })).toBeNull()
  })

  it('should_have_row_ctx_menu', async () => {
    const onReset = vi.fn(), onGenerate = vi.fn(), onFeTemplate = vi.fn()
    render(<FieldRow field={field(TTL, { userSet: true })} onCommit={() => undefined} onReset={onReset} onGenerate={onGenerate} onFeTemplate={onFeTemplate}
      feTemplates={[{ id: 'tpl', name: 'Increment', instructionIds: [] }]} />)
    await userEvent.pointer({ keys: '[MouseRight]', target: screen.getByText('TTL') })
    expect(screen.getByRole('menuitem', { name: 'Generate' })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: 'Set to default' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('menuitem', { name: 'Field engine: Increment' }))
    expect(onFeTemplate).toHaveBeenCalledWith('tpl')
    await userEvent.pointer({ keys: '[MouseRight]', target: screen.getByText('TTL') })
    await userEvent.click(screen.getByRole('menuitem', { name: 'Set to default' }))
    expect(onReset).toHaveBeenCalledTimes(1)
  })

  it('a BYTES field opens the payload editor', async () => {
    const { onCommit } = setup(field({ id: 'load', name: 'Payload', type: 'BYTES' }, { value: { vtype: 'BYTES', base64: 'eHh4' }, docValue: { vtype: 'BYTES', base64: 'eHh4' }, shown: 'xxx' }))
    await userEvent.click(screen.getByRole('button', { name: 'Payload…' }))
    expect(screen.getByRole('dialog', { name: 'Payload' })).toBeInTheDocument()
    expect(screen.getByLabelText('Text')).toHaveValue('xxx')
    await userEvent.type(screen.getByLabelText('Text'), 'y')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(onCommit).toHaveBeenCalledWith({ vtype: 'BYTES', base64: 'eHh4eQ==' })
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('a right-click inside the payload editor opens no field menu', async () => {
    setup(field({ id: 'load', name: 'Payload', type: 'BYTES' }, { shown: 'xxx' }))
    await userEvent.click(screen.getByRole('button', { name: 'Payload…' }))
    expect(fireEvent.contextMenu(screen.getByLabelText('Text'))).toBe(true)
    expect(screen.queryByRole('menuitem')).toBeNull()
  })

  it('cancelling the payload editor commits nothing', async () => {
    const { onCommit } = setup(field({ id: 'load', name: 'Payload', type: 'BYTES' }, { shown: 'xxx' }))
    await userEvent.click(screen.getByRole('button', { name: 'Payload…' }))
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onCommit).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('an expression edits as text and commits as an EXPRESSION', async () => {
    const { onCommit } = setup(field(TTL, { value: 123, docValue: { vtype: 'EXPRESSION', expr: 'RandInt()' }, userSet: true, shown: '123' }))   // the build made the expression a plain number
    const box = screen.getByRole('textbox', { name: 'TTL' })
    expect(box).toHaveValue('RandInt()')
    expect(screen.getByText('evaluated as Python on the backend')).toBeInTheDocument()
    await userEvent.clear(box)
    await userEvent.type(box, '1+2{Enter}')
    expect(onCommit).toHaveBeenCalledWith({ vtype: 'EXPRESSION', expr: '1+2' })
  })

  it('a read-only row has no controls', () => {
    setup(field(TTL, { userSet: true }), { readOnly: true })
    expect(screen.queryByRole('textbox')).toBeNull()
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.getByText('64')).toBeInTheDocument()
  })
})
