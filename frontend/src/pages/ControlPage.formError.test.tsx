import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { PortView } from '../api'
import { wireProfiles } from '../testWire'
import { trafficStore } from '../trafficStore'
import { ControlPage } from './ControlPage'

// The real Toolbar disables its Port group at the root, so a form error cannot be raised from there. This stand-in raises one
// with the root selected, to check the end state: the port is selected and its Traffic tab is open (and shows the error when the port was already selected).
vi.mock('../components/Toolbar', () => ({
  Toolbar: ({ onFormError }: { onFormError: (port: number, message: string) => void }) => (
    <button type="button" onClick={() => onFormError(0, 'bad form')}>Raise form error</button>
  ),
}))

const port0: PortView = { id: 0, state: 'STREAMS', owner: 'me', owned: true, service: false, service_filtered: false, link_up: true, speed_gbps: 10, promiscuous: false,
  multicast: false, fc_mode: 0, vlan: [], layer_cfg: {}, rx_filter_mode: null, profiles: wireProfiles('STREAMS'), rx_info: {}, info: {} }
afterEach(() => { vi.restoreAllMocks(); trafficStore.reset() })

const port1: PortView = { ...port0, id: 1 }
const connected = { type: 'session' as const, state: 'connected' as const, params: null, info: null, error: null, rpc_console: false, include_polling: false, ifaces: [], ifaces_note: null }
async function mount(ports: PortView[]) {
  const state = await import('../state')
  vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, backendOnline: true, session: connected, ports })
  render(<ControlPage />)
}

describe('ControlPage onFormError', () => {
  it('shows the error on the Traffic tab of the selected port', async () => {
    await mount([port0])
    await userEvent.click(screen.getByRole('treeitem', { name: /^Port 0,/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Raise form error' }))
    expect(screen.getByRole('tab', { name: 'Traffic' })).toHaveAttribute('aria-selected', 'true')
    expect(await screen.findByText('bad form', { selector: '[role="alert"]' })).toBeInTheDocument()
  })
  it('from another port, ends with that port selected and its Traffic tab open', async () => {
    await mount([port0, port1])
    await userEvent.click(screen.getByRole('treeitem', { name: /^Port 1,/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Raise form error' }))
    expect(screen.getByRole('treeitem', { name: /^Port 0,/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'Traffic' })).toHaveAttribute('aria-selected', 'true')
  })
})
