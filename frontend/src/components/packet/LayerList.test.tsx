import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LayerList } from './LayerList'
import type { CombinedField, CombinedLayer } from './packetModel'

afterEach(cleanup)
const fld = (id: string, shown: string): CombinedField => ({
  id, def: { id, name: id.toUpperCase() }, value: shown, shown, userSet: false, ignored: false, readOnly: false, range: null, bits: null, error: null,
})
const layer = (index: number, id: string, over: Partial<CombinedLayer> = {}): CombinedLayer =>
  ({ index, id, realId: id, offset: 0, validStructure: true, protocol: null, fields: [fld('a', `${id}-value`)], error: null, ...over })
const stack = (): CombinedLayer[] => [layer(0, 'Ether'), layer(1, 'IP'), layer(2, 'UDP')]
const props = () => ({ onToggle: vi.fn(), onMove: vi.fn(), onDelete: vi.fn() })

describe('LayerList', () => {
  it('should_preserve_collapse_state', () => {
    const p = props()
    const { rerender } = render(<LayerList layers={stack()} expanded={new Set([1])} {...p} />)
    expect(screen.getByDisplayValue('IP-value')).toBeInTheDocument()
    expect(screen.queryByDisplayValue('Ether-value')).toBeNull()
    // a rebuild hands new layers; the same panes stay open
    rerender(<LayerList layers={stack().map((l) => ({ ...l, fields: [fld('a', `${l.id}-changed`)] }))} expanded={new Set([1])} {...p} />)
    expect(screen.getByDisplayValue('IP-changed')).toBeInTheDocument()
    expect(screen.queryByDisplayValue('Ether-changed')).toBeNull()
    expect(screen.getByRole('button', { name: /IP/ })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('button', { name: /Ether/ })).toHaveAttribute('aria-expanded', 'false')
  })

  it('clicking a title toggles that pane', async () => {
    const p = props()
    render(<LayerList layers={stack()} expanded={new Set()} {...p} />)
    await userEvent.click(screen.getByRole('button', { name: /UDP/ }))
    expect(p.onToggle).toHaveBeenCalledWith(2)
  })

  it('should_have_protocol_ctx_menu', async () => {
    const p = props()
    render(<LayerList layers={stack()} expanded={new Set()} {...p} />)
    await userEvent.pointer({ keys: '[MouseRight]', target: screen.getByRole('button', { name: /^IP/ }) })
    expect(screen.getByRole('menuitem', { name: 'Move up' })).toHaveAttribute('aria-disabled', 'true')   // IP sits right under the fixed first layer
    await userEvent.click(screen.getByRole('menuitem', { name: 'Move down' }))
    expect(p.onMove).toHaveBeenCalledWith(1, 1)
    await userEvent.pointer({ keys: '[MouseRight]', target: screen.getByRole('button', { name: /^IP/ }) })
    await userEvent.click(screen.getByRole('menuitem', { name: 'Delete' }))
    expect(p.onDelete).toHaveBeenCalledWith(1)
  })

  it('the first layer is fixed', async () => {
    const p = props()
    render(<LayerList layers={stack()} expanded={new Set()} {...p} />)
    await userEvent.pointer({ keys: '[MouseRight]', target: screen.getByRole('button', { name: /^Ether/ }) })
    for (const name of ['Move up', 'Move down', 'Delete']) expect(screen.getByRole('menuitem', { name })).toHaveAttribute('aria-disabled', 'true')
  })

  it('title says as Raw when real_id differs', () => {
    render(<LayerList layers={[layer(0, 'Ether'), layer(1, 'UDP', { realId: 'Raw' })]} expanded={new Set()} {...props()} />)
    expect(screen.getByRole('button', { name: /UDP as Raw/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Ether' })).toBeInTheDocument()
  })

  it('an invalid structure gets !', () => {
    render(<LayerList layers={[layer(0, 'Ether'), layer(1, 'IP', { validStructure: false })]} expanded={new Set()} {...props()} />)
    expect(screen.getByRole('img', { name: 'invalid structure' })).toHaveTextContent('!')
  })

  it('title says not decoded when the bytes hold no such layer (real_id null), never as null', () => {
    render(<LayerList layers={[layer(0, 'Ether'), layer(1, 'DHCP', { realId: null })]} expanded={new Set()} {...props()} />)
    expect(screen.getByRole('button', { name: /DHCP \(not decoded\)/ })).toBeInTheDocument()
    expect(screen.queryByText(/as null/)).toBeNull()
  })
  it('read-only has no menu and no inputs', async () => {
    const { container } = render(<LayerList layers={stack()} expanded={new Set([0, 1, 2])} readOnly {...props()} />)
    await userEvent.pointer({ keys: '[MouseRight]', target: screen.getByRole('button', { name: /^IP/ }) })
    expect(screen.queryByRole('menu')).toBeNull()
    expect(container.querySelectorAll('input, select, textarea')).toHaveLength(0)
  })

  it('a draft stays with its layer when another is deleted', async () => {
    const p = props()
    const { rerender } = render(<LayerList layers={stack()} expanded={new Set([0, 1, 2])} {...p} />)
    const udp = screen.getByDisplayValue('UDP-value')
    await userEvent.clear(udp)
    await userEvent.type(udp, 'typed')
    rerender(<LayerList layers={[layer(0, 'Ether'), { ...layer(1, 'UDP'), index: 1 }]} expanded={new Set([0, 1])} {...p} />)
    expect(screen.getByDisplayValue('typed')).toBeInTheDocument()   // not lost, and not shown on another layer
    expect(screen.getByDisplayValue('Ether-value')).toBeInTheDocument()
  })
})
