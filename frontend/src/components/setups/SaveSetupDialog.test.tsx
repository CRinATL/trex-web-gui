import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import type { ConnectParams, PortView, TestSetup } from '../../api'
import { dashboardPrefs } from '../../dashboardPrefs'
import { pgidStore } from '../../pgidStats'
import { DEFAULT_FORM, trafficStore } from '../../trafficStore'
import { SaveSetupDialog } from './SaveSetupDialog'

afterEach(() => vi.restoreAllMocks())

const params: ConnectParams = { host: 'h', sync_port: 4501, async_port: 4500, timeout_s: 3 }
const view = (id: number, o: Partial<PortView> = {}): PortView => ({
  id, state: 'IDLE', owner: 'me', owned: true, service: false, service_filtered: false, link_up: true, speed_gbps: 10, promiscuous: false,
  multicast: false, fc_mode: 0, vlan: [], layer_cfg: {}, rx_filter_mode: null, profiles: {}, rx_info: {}, info: { hw_mac: `aa:bb:cc:dd:ee:0${id}` }, ...o,
})
const l3 = { ether: { src: 'aa:bb:cc:dd:ee:00', dst: '00:11:22:33:44:55', state: 'configured' as const }, ipv4: { state: 'resolved' as const, src: '10.0.0.1', dst: '10.0.0.2' } }
const form = (path: string, o = {}) => ({ ...DEFAULT_FORM, path, formPath: path, multValue: '10', unit: 'kpps' as const, ...o })

function open(ports: PortView[], setups: TestSetup[] = []) {
  const onClose = vi.fn()
  render(<SaveSetupDialog params={params} ports={ports} setups={setups} onClose={onClose} />)
  return onClose
}
const name = (v: string) => userEvent.type(screen.getByRole('textbox', { name: 'Name' }), v)

describe('SaveSetupDialog', () => {
  it('the footer has Cancel before Save, and becomes Cancel, Overwrite for an existing name', async () => {
    trafficStore.set(0, form('/p/0.py'))
    open([view(0)], [{ slug: 'lab', name: 'lab' } as TestSetup])
    const ends = () => [...screen.getByRole('dialog').querySelectorAll('.dialog-footer .end button')].map((b) => b.textContent)
    expect(ends()).toEqual(['Cancel', 'Save'])
    await name('lab')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(ends()).toEqual(['Cancel', 'Overwrite'])
  })
  it('only ports with a profile are offered; the layer and stop_first checkboxes fill the fields', async () => {
    trafficStore.set(0, form('/p/0.py'))      // port 1 has no profile in its form
    const put = vi.spyOn(api, 'putSetup').mockResolvedValue({} as TestSetup)
    const onClose = open([view(0, { layer_cfg: l3 }), view(1)])
    expect(screen.getByRole('checkbox', { name: /Port 0/ })).toBeChecked()
    expect(screen.queryByRole('checkbox', { name: /Port 1/ })).toBeNull()
    expect(screen.getByRole('textbox', { name: 'Server' })).toHaveValue('h:4501')
    await name('Lab A: 10 kpps')
    await userEvent.click(screen.getByRole('checkbox', { name: /include port configuration/ }))
    await userEvent.click(screen.getByRole('checkbox', { name: /stop a transmitting port/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(put).toHaveBeenCalledTimes(1)
    const [slug, body] = put.mock.calls[0]
    expect(slug).toBe('lab-a-10-kpps')
    expect(body).toMatchObject({ name: 'Lab A: 10 kpps', server: 'h:4501', dashboard: null })
    expect(body.ports).toHaveLength(1)
    expect(body.ports[0]).toMatchObject({ port: 0, stop_first: true, acquire_force: false, mult: '10kpps', profile: { path: '/p/0.py' },
      layer: { mode: 'L3', src_ipv4: '10.0.0.1', dst_ipv4: '10.0.0.2' } })
    expect(onClose).toHaveBeenCalled()
  })
  it('leaves the layer and stop_first out by default, and adds the dashboard when asked', async () => {
    trafficStore.set(0, form('/p/0.py'))
    dashboardPrefs.set({ tab: 'streams', windowS: 60, layout: 4 })
    pgidStore.setSelection('streams', { picked: [{ id: 5, color: 'c' }], seeded: true })
    const put = vi.spyOn(api, 'putSetup').mockResolvedValue({} as TestSetup)
    open([view(0, { layer_cfg: l3 })])
    await name('x')
    await userEvent.click(screen.getByRole('checkbox', { name: /include dashboard layout/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    const body = put.mock.calls[0][1]
    expect(body.ports[0]).toMatchObject({ stop_first: false, layer: null })
    expect(body.dashboard).toEqual({ tab: 'streams', window_s: 60, layout: 4, streams_pgids: [5], latency_pgids: [] })
  })
  it('names a port whose layer cannot be stored when port configuration is ticked', async () => {
    trafficStore.set(0, form('/p/0.py'))
    open([view(0), view(1)])      // layer_cfg {}: an L2 port with no destination
    expect(screen.queryByText(/layer not included/)).toBeNull()
    await userEvent.click(screen.getByRole('checkbox', { name: /include port configuration/ }))
    expect(screen.getByText('Port 0: layer not included: no destination MAC')).toBeInTheDocument()
  })
  it('a port with an invalid multiplier is named and not offered', () => {
    trafficStore.set(0, form('/p/0.py', { multValue: '0' }))
    trafficStore.set(1, form('/p/1.py'))
    open([view(0), view(1)])
    expect(screen.getByText(/Port 0.*multiplier is not valid/)).toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: /Port 0/ })).toBeNull()
    expect(screen.getByRole('checkbox', { name: /Port 1/ })).toBeChecked()
  })
  it('saving a name whose slug exists asks before overwriting', async () => {
    trafficStore.set(0, form('/p/0.py'))
    const put = vi.spyOn(api, 'putSetup').mockResolvedValue({} as TestSetup)
    const existing: TestSetup = { name: 'lab a', slug: 'lab-a', created: 'c', updated: 'u', server: null, ports: [], dashboard: null }
    open([view(0)], [existing])
    await name('Lab A')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(put).not.toHaveBeenCalled()
    expect(screen.getByText(/already exists/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Overwrite' }))
    expect(put).toHaveBeenCalledTimes(1)
  })
  it('refuses a name with no letter or digit, and shows the server refusal', async () => {
    trafficStore.set(0, form('/p/0.py'))
    const put = vi.spyOn(api, 'putSetup').mockRejectedValue(new api.ApiError(422, 'invalid_setup', 'server: bad'))
    open([view(0)])
    await name('!!!')
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    await userEvent.clear(screen.getByRole('textbox', { name: 'Name' }))
    await name('ok')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(put).toHaveBeenCalled()
    expect(await screen.findByRole('alert')).toHaveTextContent('server: bad')
  })
})
