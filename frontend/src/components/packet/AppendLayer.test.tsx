import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import type { Definitions, PacketDoc } from '../../api'
import defsFixture from '../../__fixtures__/packet/definitions.json'
import { AppendLayer } from './AppendLayer'

vi.mock('../../api', async (orig) => ({ ...(await orig<typeof import('../../api')>()), payloadClasses: vi.fn() }))
afterEach(() => { cleanup(); vi.clearAllMocks() })

const defs = (defsFixture as unknown as { definitions: Definitions }).definitions
const doc: PacketDoc = { fileType: 'trex-packet-editor', version: '1.0.0', metadata: { caption: '' }, fePrarameters: {}, feInstructions: [], packet: [{ id: 'Ether', fields: [] }] }

describe('AppendLayer', () => {
  it('should_build_tcpip_stack_with_enter_or_btn', async () => {
    vi.mocked(api.payloadClasses).mockResolvedValue({ classes: [] })
    const onAppend = vi.fn()
    render(<AppendLayer doc={doc} defs={defs} onAppend={onAppend} />)
    const box = screen.getByRole('combobox', { name: 'Add layer' })
    await userEvent.type(box, 'IP{Enter}')
    expect(onAppend).toHaveBeenLastCalledWith('IP')
    expect(box).toHaveValue('')
    await userEvent.type(box, 'TCP')
    await userEvent.click(screen.getByRole('button', { name: 'Add' }))
    expect(onAppend).toHaveBeenLastCalledWith('TCP')
    expect(onAppend).toHaveBeenCalledTimes(2)
  })

  it('an unknown protocol is not appended', async () => {
    vi.mocked(api.payloadClasses).mockResolvedValue({ classes: [] })
    const onAppend = vi.fn()
    render(<AppendLayer doc={doc} defs={defs} onAppend={onAppend} />)
    await userEvent.type(screen.getByRole('combobox', { name: 'Add layer' }), 'NoSuchLayer{Enter}')
    expect(onAppend).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('unknown protocol "NoSuchLayer"')
  })

  it('payload classes come first', async () => {
    // Foo has no definition and so is not offered; UDP and TCP are what IP can carry
    vi.mocked(api.payloadClasses).mockResolvedValue({ classes: ['UDP', 'Foo', 'TCP'] })
    const { container } = render(<AppendLayer doc={doc} defs={defs} onAppend={() => undefined} />)
    await waitFor(() => expect(container.querySelector('datalist option')?.getAttribute('value')).toBe('UDP'))
    const values = [...container.querySelectorAll('datalist option')].map((o) => o.getAttribute('value'))
    expect(values.slice(0, 2)).toEqual(['UDP', 'TCP'])
    expect(values).not.toContain('Foo')
    expect(values.length).toBe(defs.layerIds.length)         // then every protocol, each once
    expect(new Set(values).size).toBe(values.length)
    expect(values.slice(2)).toContain('Ether')
  })
})
