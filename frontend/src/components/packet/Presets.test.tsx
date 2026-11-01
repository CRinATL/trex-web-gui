import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import { ApiError, type PacketDoc, type PresetInfo, type PresetResult } from '../../api'
import { Presets } from './Presets'

vi.mock('../../api', async (orig) => ({ ...(await orig<typeof import('../../api')>()), listPresets: vi.fn(), buildPreset: vi.fn() }))

// The listing as the backend sends it (GET /api/studio/packet/presets).
const mac = (value: string) => ({ mode: 'trex_config', value, count: 1, step: 1 })
const presets: PresetInfo[] = [
  { id: 'flood', name: 'Flood', defaults: { payload: 'x', l4: 'udp', src_mac: mac('00:00:00:00:00:01'), dst_mac: mac('00:00:00:00:00:02'), src_ip: '16.0.0.1', dst_ip: '48.0.0.1', sport: 1025, dport: 12, frame: { mode: 'fixed', min: 64, max: 1518 } } },
  { id: 'ip_sweep', name: 'IP sweep', defaults: { which: 'both', src: { start: '16.0.0.1', step: 1, op: 'inc', count: 254 }, dst: { start: '48.0.0.1', step: 1, op: 'inc', count: 254 }, checksum: 'ipv4' } },
  { id: 'frame_size_sweep', name: 'Frame-size sweep', defaults: { min: 64, max: 1518, step: 1, op: 'inc' } },
  { id: 'vlan', name: 'VLAN', defaults: { tags: [{ vlan: 100, prio: 0 }] } },
]
const doc: PacketDoc = { fileType: 'trex-packet-editor', version: '1.0.0', metadata: { caption: '' }, fePrarameters: {}, feInstructions: [], packet: [{ id: 'Ether', fields: [] }] }
const result = (flags: number | null): PresetResult => ({ doc, flags })

// Selects the preset's tab, then scopes queries to its form (only the selected tab's form is shown).
const form = async (name: string) => {
  await userEvent.click(await screen.findByRole('tab', { name }))
  return within(screen.getByRole('region', { name }))
}
const sent = () => vi.mocked(api.buildPreset).mock.calls.at(-1)!

beforeEach(() => {
  vi.mocked(api.listPresets).mockResolvedValue({ presets })
  vi.mocked(api.buildPreset).mockResolvedValue(result(null))
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('Presets', () => {
  it('one tab per preset; only the selected preset form shows, and a form keeps its edits across tabs', async () => {
    render(<Presets onOpen={vi.fn()} />)
    const tabs = await screen.findAllByRole('tab')
    expect(tabs.map((t) => t.textContent)).toEqual(['Flood', 'IP sweep', 'Frame-size sweep', 'VLAN'])
    expect(screen.getByRole('tab', { name: 'Flood' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getAllByRole('region').map((r) => r.getAttribute('data-preset'))).toEqual(['flood'])

    const flood = within(screen.getByRole('region', { name: 'Flood' }))
    await userEvent.clear(flood.getByLabelText('Destination port'))
    await userEvent.type(flood.getByLabelText('Destination port'), '8080')
    await userEvent.click(screen.getByRole('tab', { name: 'VLAN' }))
    expect(screen.getAllByRole('region').map((r) => r.getAttribute('data-preset'))).toEqual(['vlan'])
    await userEvent.click(screen.getByRole('tab', { name: 'Flood' }))
    expect(within(screen.getByRole('region', { name: 'Flood' })).getByLabelText('Destination port')).toHaveValue('8080')
  })

  it('flood form sends its params', async () => {
    render(<Presets onOpen={vi.fn()} />)
    const f = await form('Flood')
    await userEvent.click(f.getByRole('button', { name: 'Open in editor' }))
    await waitFor(() => expect(api.buildPreset).toHaveBeenCalledTimes(1))
    expect(sent()).toEqual(['flood', { l4: 'udp', src_mac: { mode: 'trex_config' }, dst_mac: { mode: 'trex_config' }, src_ip: '16.0.0.1', dst_ip: '48.0.0.1', sport: 1025, dport: 12, payload: 'x', frame: { mode: 'fixed', min: 64, max: 64 } }])

    await userEvent.selectOptions(f.getByLabelText('Protocol'), 'tcp')
    await userEvent.selectOptions(f.getByLabelText('Source MAC'), 'inc')
    expect(f.queryByLabelText('Destination MAC count')).toBeNull()      // only a stepped MAC asks how many
    await userEvent.clear(f.getByLabelText('Source MAC address'))
    await userEvent.type(f.getByLabelText('Source MAC address'), '02:00:00:00:00:10')
    await userEvent.clear(f.getByLabelText('Source MAC count'))
    await userEvent.type(f.getByLabelText('Source MAC count'), '16')
    await userEvent.clear(f.getByLabelText('Source MAC step'))
    await userEvent.type(f.getByLabelText('Source MAC step'), '2')
    await userEvent.selectOptions(f.getByLabelText('Destination MAC'), 'packet')
    await userEvent.clear(f.getByLabelText('Destination port'))
    await userEvent.type(f.getByLabelText('Destination port'), '8080')
    await userEvent.selectOptions(f.getByLabelText('Frame size'), 'random')
    await userEvent.clear(f.getByLabelText('Smallest frame (with FCS)'))
    await userEvent.type(f.getByLabelText('Smallest frame (with FCS)'), '100')
    await userEvent.clear(f.getByLabelText('Largest frame (with FCS)'))
    await userEvent.type(f.getByLabelText('Largest frame (with FCS)'), '900')
    await userEvent.click(f.getByRole('button', { name: 'Open in editor' }))
    await waitFor(() => expect(api.buildPreset).toHaveBeenCalledTimes(2))
    expect(sent()).toEqual(['flood', {
      l4: 'tcp', src_mac: { mode: 'inc', value: '02:00:00:00:00:10', count: 16, step: 2 }, dst_mac: { mode: 'packet', value: '00:00:00:00:00:02' },
      src_ip: '16.0.0.1', dst_ip: '48.0.0.1', sport: 1025, dport: 8080, payload: 'x', frame: { mode: 'random', min: 100, max: 900 },
    }])
  })

  it('ip sweep takes count or end', async () => {
    render(<Presets onOpen={vi.fn()} />)
    const f = await form('IP sweep')
    await userEvent.selectOptions(f.getByLabelText('Sweep'), 'src')
    expect(f.queryByLabelText('Destination first address')).toBeNull()
    await userEvent.click(f.getByRole('button', { name: 'Open in editor' }))
    await waitFor(() => expect(api.buildPreset).toHaveBeenCalledTimes(1))
    expect(sent()).toEqual(['ip_sweep', { which: 'src', checksum: 'ipv4', src: { start: '16.0.0.1', step: 1, op: 'inc', count: 254 } }])

    await userEvent.selectOptions(f.getByLabelText('Source range by'), 'end')
    expect(f.queryByLabelText('Source count')).toBeNull()
    await userEvent.clear(f.getByLabelText('Source last address'))
    await userEvent.type(f.getByLabelText('Source last address'), '16.0.0.10')
    await userEvent.selectOptions(f.getByLabelText('Checksum'), 'hw')
    await userEvent.click(f.getByRole('button', { name: 'Open in editor' }))
    await waitFor(() => expect(api.buildPreset).toHaveBeenCalledTimes(2))
    const body = sent()[1] as { src: Record<string, unknown> }
    expect(body).toEqual({ which: 'src', checksum: 'hw', src: { start: '16.0.0.1', step: 1, op: 'inc', end: '16.0.0.10' } })
    expect('count' in body.src).toBe(false)                              // exactly one of count and end

    await userEvent.selectOptions(f.getByLabelText('Sweep'), 'both')
    await userEvent.selectOptions(f.getByLabelText('Source range by'), 'count')
    await userEvent.click(f.getByRole('button', { name: 'Open in editor' }))
    await waitFor(() => expect(api.buildPreset).toHaveBeenCalledTimes(3))
    expect(sent()[1]).toMatchObject({ which: 'both', src: { count: 254 }, dst: { start: '48.0.0.1', count: 254 } })
  })

  it('frame-size sweep and vlan send their params', async () => {
    render(<Presets onOpen={vi.fn()} />)
    const sweep = await form('Frame-size sweep')
    await userEvent.selectOptions(sweep.getByLabelText('Order'), 'dec')
    await userEvent.click(sweep.getByRole('button', { name: 'Open in editor' }))
    await waitFor(() => expect(api.buildPreset).toHaveBeenCalledTimes(1))
    expect(sent()).toEqual(['frame_size_sweep', { min: 64, max: 1518, step: 1, op: 'dec' }])
    const vlan = await form('VLAN')
    await userEvent.selectOptions(vlan.getByLabelText('Tags'), '2')
    await userEvent.clear(vlan.getByLabelText('VLAN 2'))
    await userEvent.type(vlan.getByLabelText('VLAN 2'), '200')
    await userEvent.clear(vlan.getByLabelText('Priority 2'))
    await userEvent.type(vlan.getByLabelText('Priority 2'), '5')
    await userEvent.click(vlan.getByRole('button', { name: 'Open in editor' }))
    await waitFor(() => expect(api.buildPreset).toHaveBeenCalledTimes(2))
    expect(sent()).toEqual(['vlan', { tags: [{ vlan: 100, prio: 0 }, { vlan: 200, prio: 5 }] }])
    await userEvent.selectOptions(vlan.getByLabelText('Tags'), '1')
    await userEvent.click(vlan.getByRole('button', { name: 'Open in editor' }))
    await waitFor(() => expect(api.buildPreset).toHaveBeenCalledTimes(3))
    expect(sent()).toEqual(['vlan', { tags: [{ vlan: 100, prio: 0 }] }])
  })

  it('open passes doc and flags', async () => {
    const onOpen = vi.fn()
    vi.mocked(api.buildPreset).mockResolvedValue(result(3))
    render(<Presets onOpen={onOpen} />)
    const f = await form('Flood')
    await userEvent.click(f.getByRole('button', { name: 'Open in editor' }))
    await waitFor(() => expect(onOpen).toHaveBeenCalledTimes(1))
    expect(onOpen).toHaveBeenCalledWith(doc, 3)
    vi.mocked(api.buildPreset).mockResolvedValue(result(null))
    await userEvent.click((await form('VLAN')).getByRole('button', { name: 'Open in editor' }))
    await waitFor(() => expect(onOpen).toHaveBeenCalledTimes(2))
    expect(onOpen).toHaveBeenLastCalledWith(doc, null)
  })

  it('a number that is not whole is refused in the form, a refusal from the backend is shown', async () => {
    const onOpen = vi.fn()
    render(<Presets onOpen={onOpen} />)
    const f = await form('Flood')
    await userEvent.clear(f.getByLabelText('Source port'))
    await userEvent.type(f.getByLabelText('Source port'), '80x')
    await userEvent.click(f.getByRole('button', { name: 'Open in editor' }))
    expect(f.getByRole('alert')).toHaveTextContent('Source port must be a whole number')
    expect(api.buildPreset).not.toHaveBeenCalled()
    await userEvent.clear(f.getByLabelText('Source port'))
    await userEvent.type(f.getByLabelText('Source port'), '80')
    vi.mocked(api.buildPreset).mockRejectedValue(new ApiError(422, 'invalid_params', 'src_ip: not an IPv4 address'))
    await userEvent.click(f.getByRole('button', { name: 'Open in editor' }))
    expect(await f.findByRole('alert')).toHaveTextContent('src_ip: not an IPv4 address')
    expect(onOpen).not.toHaveBeenCalled()
    expect(f.getByRole('button', { name: 'Open in editor' })).toBeEnabled()
  })

  it('a refusal that arrives on another tab marks that tab while another tab shows', async () => {
    let refuse: (e: unknown) => void = () => {}
    vi.mocked(api.buildPreset).mockReturnValue(new Promise((_, reject) => { refuse = reject }))
    render(<Presets onOpen={vi.fn()} />)
    const f = await form('Flood')
    await userEvent.click(f.getByRole('button', { name: 'Open in editor' }))
    await userEvent.click(screen.getByRole('tab', { name: 'VLAN' }))      // the user moves on while the build runs
    refuse(new ApiError(422, 'invalid_params', 'src_ip: not an IPv4 address'))
    const flood = await screen.findByRole('tab', { name: 'Flood (refused)' })
    expect(flood).toHaveTextContent('!')
    expect(screen.getByRole('tab', { name: 'VLAN' })).toHaveAttribute('aria-selected', 'true')   // the user's tab stays
    await userEvent.click(flood)
    expect(within(screen.getByRole('region', { name: 'Flood' })).getByRole('alert')).toHaveTextContent('src_ip: not an IPv4 address')
    expect(screen.getByRole('tab', { name: 'Flood' })).not.toHaveTextContent('!')                // shown where it belongs now
  })
  it('one request at a time, and nothing opens after the page is gone', async () => {
    const onOpen = vi.fn()
    let finish: (r: PresetResult) => void = () => {}
    vi.mocked(api.buildPreset).mockReturnValue(new Promise<PresetResult>((resolve) => { finish = resolve }))
    const { unmount } = render(<Presets onOpen={onOpen} />)
    const f = await form('Flood')
    await userEvent.click(f.getByRole('button', { name: 'Open in editor' }))
    await waitFor(() => expect(f.getByRole('button', { name: 'Open in editor' })).toBeDisabled())
    expect((await form('VLAN')).getByRole('button', { name: 'Open in editor' })).toBeDisabled()
    unmount()
    finish(result(1))
    await new Promise((r) => setTimeout(r, 10))
    expect(onOpen).not.toHaveBeenCalled()
  })

  it('a listing that cannot be loaded says so', async () => {
    vi.mocked(api.listPresets).mockRejectedValue(new ApiError(500, 'boom', 'the backend is down'))
    render(<Presets onOpen={vi.fn()} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('The presets could not be loaded: the backend is down')
  })
})
