import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import * as state from '../state'
import { wireProfiles } from '../testWire'
import { PortNameField } from './PortNameField'

afterEach(() => vi.restoreAllMocks())

const params = { host: 'h', sync_port: 4501, async_port: 4500, timeout_s: 3, read_only: false }
const session = { type: 'session' as const, state: 'connected' as const, params: null, info: null, error: null, rpc_console: false, include_polling: false, ifaces: [], ifaces_note: null }
// no hw_mac, so the label key is the port id: '#0'
const port: api.PortView = { id: 0, state: 'IDLE', owner: 'me', owned: true, service: false, service_filtered: false, link_up: true, speed_gbps: 10, promiscuous: false,
  multicast: false, fc_mode: -1, vlan: [], layer_cfg: {}, rx_filter_mode: null, profiles: wireProfiles('IDLE'), rx_info: {}, info: { driver: 'd' } }
const kernel = (confirmed: boolean): api.PortIface => ({ port: 0, cfg_entry: 'veth0', kind: 'af_packet', kernel_name: 'veth0', pci_addr: null, driver: null, vendor_device: null, model: null, confirmed, note: null })

interface Opt { suggestName?: string | null; initial?: { name: string; description: string } | null; loadError?: string | null; params?: typeof params | null; focus?: boolean; onFocused?: () => void }
// The state a port named 'initial' and suggested 'suggestName' would produce
const stateFor = (o: Opt) => ({
  ...state.initialState,
  session: { ...session, params: o.params === undefined ? params : o.params, ifaces: o.suggestName ? [{ ...kernel(true), kernel_name: o.suggestName }] : [] },
  ports: [port], portLabels: (o.initial ? { 'h:4501': { '#0': o.initial } } : {}) as Record<string, Record<string, { name: string; description: string }>>, portLabelsError: o.loadError ?? null,
})
const open = (o: Opt = {}) => {
  const spy = vi.spyOn(state, 'useAppState').mockReturnValue(stateFor(o))
  const r = render(<PortNameField port={port} ports={[port]} focus={o.focus} onFocused={o.onFocused} />)
  return { ...r, spy, again: (n: Opt) => { spy.mockReturnValue(stateFor(n)); r.rerender(<PortNameField port={port} ports={[port]} />) } }
}
const nameBox = () => screen.getByRole('textbox', { name: /^Name/ })
const save = () => screen.getByRole('button', { name: 'Save' })

describe('PortNameField', () => {
  it('a suggested name with no saved label is marked and counted, as Save is enabled (R6)', () => {
    open({ suggestName: 'enp3s0f0' })
    expect(save()).toBeEnabled()
    expect(nameBox()).toHaveClass('edited')
    expect(screen.getByText('1 unsaved change')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: /^Description/ })).not.toHaveClass('edited')
  })
  it('a field changed from the saved label is marked and counted beside Save (RC1-31)', async () => {
    open({ initial: { name: 'uplink', description: 'to the DUT' } })
    const desc = screen.getByRole('textbox', { name: /^Description/ })
    expect(nameBox()).not.toHaveClass('edited')
    await userEvent.type(nameBox(), '2')
    expect(nameBox()).toHaveClass('edited')
    expect(desc).not.toHaveClass('edited')
    expect(nameBox()).toHaveAccessibleDescription('7/40 edited')
    expect(screen.getByText('1 unsaved change')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Revert' }))
    expect(nameBox()).not.toHaveClass('edited')
    expect(screen.queryByText('edited')).not.toBeInTheDocument()
  })
  it('pre-fills the name with the suggested kernel name when the port has no label, still editable', async () => {
    const put = vi.spyOn(api, 'putPortLabel').mockResolvedValue({ labels: {}, load_error: null })
    open({ suggestName: 'enp3s0f0' })
    expect(nameBox()).toHaveValue('enp3s0f0')
    await userEvent.type(nameBox(), '-x')
    await userEvent.click(save())
    expect(put).toHaveBeenCalledWith({ server: 'h:4501', key: '#0', name: 'enp3s0f0-x', description: '' })
  })
  it('a saved label wins over the suggestion', () => {
    open({ initial: { name: 'uplink', description: '' }, suggestName: 'enp3s0f0' })
    expect(nameBox()).toHaveValue('uplink')
  })
  it('a new suggestion does not overwrite a name already typed', async () => {
    const { again } = open({ suggestName: null })
    await userEvent.type(nameBox(), 'mine')
    again({ suggestName: 'enp3s0f0' })
    expect(nameBox()).toHaveValue('mine')
  })
  it('saves the typed name and description under the server and key', async () => {
    const put = vi.spyOn(api, 'putPortLabel').mockResolvedValue({ labels: {}, load_error: null })
    open()
    await userEvent.type(nameBox(), 'uplink-A')
    await userEvent.type(screen.getByRole('textbox', { name: /^Description/ }), 'to the DUT')
    await userEvent.click(save())
    expect(put).toHaveBeenCalledWith({ server: 'h:4501', key: '#0', name: 'uplink-A', description: 'to the DUT' })
  })
  it('starts from the existing label', () => {
    open({ initial: { name: 'old', description: 'd' } })
    expect(nameBox()).toHaveValue('old')
    expect(screen.getByRole('textbox', { name: /^Description/ })).toHaveValue('d')
  })
  it('shows the 422 reason inline and keeps the text', async () => {
    vi.spyOn(api, 'putPortLabel').mockRejectedValue(new api.ApiError(422, 'invalid_label', 'name contains a control character'))
    open()
    await userEvent.type(nameBox(), 'x')
    await userEvent.click(save())
    expect(await screen.findByRole('alert')).toHaveTextContent('name contains a control character')
    expect(nameBox()).toHaveValue('x')
  })
  it('counts code points and disables Save past the limit', async () => {
    open()
    // 40 emoji are 80 UTF-16 units but 40 code points: allowed
    await userEvent.click(nameBox())
    await userEvent.paste('😀'.repeat(40))
    expect(screen.getByText(/40\/40/)).toBeInTheDocument()
    expect(save()).toBeEnabled()
    await userEvent.paste('😀')
    expect(save()).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('limited to 40')
  })
  it('shows a load error from the labels file, and none otherwise', () => {
    open({ loadError: 'bad toml at line 3' })
    expect(screen.getByRole('status')).toHaveTextContent('bad toml at line 3')
  })
  it('shows no notice without a load error', () => {
    open()
    expect(screen.queryByRole('status')).toBeNull()
  })
  // The dialog's Cancel is Revert here, and there is no dialog for Escape to close.
  it('Escape leaves the edit alone; Revert restores the saved label', async () => {
    open({ initial: { name: 'old', description: '' } })
    await userEvent.type(nameBox(), '-edit')
    await userEvent.keyboard('{Escape}')
    expect(nameBox()).toHaveValue('old-edit')
    await userEvent.click(screen.getByRole('button', { name: 'Revert' }))
    expect(nameBox()).toHaveValue('old')
  })
  it('replaces line breaks typed or pasted into either field with a space', async () => {
    const put = vi.spyOn(api, 'putPortLabel').mockResolvedValue({ labels: {}, load_error: null })
    open()
    await userEvent.click(nameBox())
    await userEvent.paste('a\r\nb')
    await userEvent.click(screen.getByRole('textbox', { name: /^Description/ }))
    await userEvent.paste('line one\nline two\rthree')
    await userEvent.click(save())
    expect(put).toHaveBeenCalledWith({ server: 'h:4501', key: '#0', name: 'a b', description: 'line one line two three' })
  })
  it('Name… pre-fills a confirmed kernel name when the port has no label, but a saved label wins', () => {
    const { unmount } = open({ suggestName: 'veth0' })
    expect(nameBox()).toHaveValue('veth0')
    unmount()
    open({ suggestName: 'veth0', initial: { name: 'uplink-A', description: '' } })
    expect(nameBox()).toHaveValue('uplink-A')
  })
  it('Name… does not pre-fill from an unconfirmed entry, even one carrying a kernel_name', () => {
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...stateFor({}), session: { ...session, params, ifaces: [kernel(false)] } })
    render(<PortNameField port={port} ports={[port]} />)
    expect(nameBox()).toHaveValue('')
  })
  it('Save is disabled until the field differs', async () => {
    open({ initial: { name: 'uplink', description: '' } })
    expect(save()).toBeDisabled()
    await userEvent.type(nameBox(), 'x')
    expect(save()).toBeEnabled()
    await userEvent.type(nameBox(), '{Backspace}')
    expect(save()).toBeDisabled()
  })
  it('Revert restores the saved label', async () => {
    open({ initial: { name: 'uplink', description: 'to the DUT' } })
    const revert = screen.getByRole('button', { name: 'Revert' })
    expect(revert).toBeDisabled()
    await userEvent.type(nameBox(), 'zz')
    await userEvent.type(screen.getByRole('textbox', { name: /^Description/ }), 'zz')
    await userEvent.click(revert)
    expect(nameBox()).toHaveValue('uplink')
    expect(screen.getByRole('textbox', { name: /^Description/ })).toHaveValue('to the DUT')
    expect(save()).toBeDisabled()
  })
  it('without a connection the inputs are disabled and say to connect', () => {
    open({ params: null })
    expect(nameBox()).toBeDisabled()
    expect(screen.getByRole('textbox', { name: /^Description/ })).toBeDisabled()
    expect(screen.getByText(/Connect to name ports/)).toBeInTheDocument()
    expect(save()).toBeDisabled()
  })
  it('focus moves to the Name input once and reports it', async () => {
    const onFocused = vi.fn()
    const { again } = open({ focus: true, onFocused })
    expect(nameBox()).toHaveFocus()
    expect(onFocused).toHaveBeenCalledTimes(1)
    await userEvent.click(document.body)
    expect(nameBox()).not.toHaveFocus()
    again({})   // a later render does not pull focus back
    expect(nameBox()).not.toHaveFocus()
    expect(onFocused).toHaveBeenCalledTimes(1)
  })
  it('saves under the MAC key when the port has a unique usable MAC', async () => {
    const put = vi.spyOn(api, 'putPortLabel').mockResolvedValue({ labels: {}, load_error: null })
    const withMac = { ...port, info: { driver: 'd', hw_mac: '8E:4E:97:11:BF:A8' } }
    vi.spyOn(state, 'useAppState').mockReturnValue(stateFor({}))
    render(<PortNameField port={withMac} ports={[withMac]} />)
    await userEvent.type(nameBox(), 'uplink-B')
    await userEvent.click(save())
    expect(put).toHaveBeenCalledWith({ server: 'h:4501', key: '8e:4e:97:11:bf:a8', name: 'uplink-B', description: '' })
  })
  it('a label saved elsewhere replaces an unedited field', () => {
    const { again } = open({ initial: { name: 'old', description: '' } })
    again({ initial: { name: 'new', description: 'd' } })
    expect(nameBox()).toHaveValue('new')
    expect(screen.getByRole('textbox', { name: /^Description/ })).toHaveValue('d')
    expect(save()).toBeDisabled()
  })
  it('a label saved elsewhere does not replace an edit', async () => {
    const { again } = open({ initial: { name: 'old', description: '' } })
    await userEvent.type(nameBox(), '-mine')
    again({ initial: { name: 'new', description: '' } })
    expect(nameBox()).toHaveValue('old-mine')
  })
  it('a label saved elsewhere replaces the untouched field even while the other holds an edit', async () => {
    const { again } = open({ initial: { name: 'old', description: 'd0' } })
    await userEvent.type(nameBox(), '-mine')
    again({ initial: { name: 'old', description: 'd1' } })
    expect(nameBox()).toHaveValue('old-mine')
    expect(screen.getByRole('textbox', { name: /^Description/ })).toHaveValue('d1')
    again({ initial: { name: 'new', description: 'd1' } })
    expect(nameBox()).toHaveValue('old-mine')
    expect(screen.getByRole('textbox', { name: /^Description/ })).toHaveValue('d1')
  })
  it('a label saved elsewhere replaces an untouched Name even while Description holds an edit', async () => {
    const { again } = open({ initial: { name: 'n0', description: 'old' } })
    const desc = screen.getByRole('textbox', { name: /^Description/ })
    await userEvent.type(desc, '-mine')
    again({ initial: { name: 'n1', description: 'old' } })
    expect(desc).toHaveValue('old-mine')
    expect(nameBox()).toHaveValue('n1')
    again({ initial: { name: 'n1', description: 'new' } })
    expect(desc).toHaveValue('old-mine')
    expect(nameBox()).toHaveValue('n1')
  })
  it('Name and Description are rows of one form grid', () => {
    open()
    const grid = nameBox().closest('.form-grid')
    expect(grid).not.toBeNull()
    expect(screen.getByLabelText('Description').closest('.form-grid')).toBe(grid)
    expect(screen.getByRole('button', { name: 'Save' }).closest('.form-grid')).toBeNull()   // the action bar is under the form
  })
  it('after Save the trimmed label leaves Save and Revert disabled', async () => {
    vi.spyOn(api, 'putPortLabel').mockResolvedValue({ labels: {}, load_error: null })
    const { again } = open()
    await userEvent.type(nameBox(), 'uplink ')
    await userEvent.click(save())
    again({ initial: { name: 'uplink', description: '' } })   // the server strips the value, then publishes it
    expect(nameBox()).toHaveValue('uplink')
    expect(save()).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Revert' })).toBeDisabled()
  })
})
