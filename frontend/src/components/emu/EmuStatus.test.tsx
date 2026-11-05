import { act, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { EmuMsg } from '../../api'
import { emuStore } from '../../emuStore'
import emuMsg from '../../__fixtures__/emuMsg.json'
import { EmuStatus } from './EmuStatus'

const connected = emuMsg.connected as EmuMsg

describe('EmuStatus', () => {
  it('names the state, the server version and the generation; a loss shows its cause', () => {
    act(() => emuStore.push({ ...connected, state: 'disconnected', target: null, info: null }))
    render(<EmuStatus />)
    expect(screen.getByRole('status')).toHaveTextContent('Disconnected')
    act(() => emuStore.push(connected))
    expect(screen.getByRole('status')).toHaveTextContent('Connected')
    expect(screen.getByText(/version v0\.1 · generation 0/)).toBeInTheDocument()
    expect(screen.getByText('EMU server 127.0.0.1:4510')).toBeInTheDocument()
    act(() => emuStore.push({ ...connected, busy: { kind: 'load', done: 3, total: 9 } }))
    expect(screen.getByText('load: 3 of 9')).toBeInTheDocument()
    act(() => emuStore.push({ ...connected, state: 'lost', error: 'timed out' }))
    expect(screen.getByRole('status')).toHaveTextContent('Connection lost')
    expect(screen.getByRole('alert')).toHaveTextContent('timed out')
  })
})
