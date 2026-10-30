import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import { wireLayerPort, wirePort } from '../testWire'
import { PortConfigTab } from './PortConfigTab'

let busy: Record<number, api.BusyKind> = {}
vi.mock('../state', () => ({
  useBusy: () => busy, usePingLog: () => [], usePingRunner: () => vi.fn(),
  useNeighbours: () => undefined, useNeighbourActions: () => ({ set: vi.fn(), clear: vi.fn() }),
}))
beforeEach(() => { busy = {} })
afterEach(() => vi.restoreAllMocks())

const PEER = '52:52:18:89:91:ea'
const SRC = '8e:4e:97:11:bf:a8'
const ok = (o: Partial<Awaited<ReturnType<typeof api.applyLayer>>> = {}) =>
  vi.spyOn(api, 'applyLayer').mockResolvedValue({ ports: [], arp: null, service_restore_error: null, ...o })
// an owned view so the form is usable
const owned = (name: Parameters<typeof wireLayerPort>[0], o: Partial<api.PortView> = {}) => wireLayerPort(name, { owned: true, ...o })

describe('PortConfigTab', () => {
  it("each control shows the fixture view's value", () => {
    const radio = (name: string) => screen.getByRole('radiogroup', { name }).querySelector<HTMLInputElement>('input:checked')?.parentElement?.textContent?.trim()
    const val = (label: string) => { const e = screen.getByLabelText(label); return e instanceof HTMLInputElement ? e.value : e.textContent }
    const expected: Record<string, Record<string, string | undefined>> = {
      l2: { Mode: 'L2', 'Source MAC': SRC, 'Destination MAC': PEER, 'Outer VLAN': '', IPv6: 'Off' },
      l3_resolved: { Mode: 'L3', 'Destination MAC': PEER, 'Source IPv4': '1.1.1.1', 'Destination IPv4': '2.2.2.2' },
      l3_unresolved: { Mode: 'L3', 'Destination MAC': 'unresolved', 'Destination IPv4': '1.1.1.99' },
      vlan_two_tags: { 'Outer VLAN': '10', 'Inner VLAN': '20' },
      ipv6_auto: { IPv6: 'Auto' },
      ipv6_address: { IPv6: 'Address', 'IPv6 source': 'fe80::5052:18ff:fe89:91ea' },
    }
    for (const [name, rows] of Object.entries(expected)) {
      const { unmount } = render(<PortConfigTab port={wireLayerPort(name as Parameters<typeof wireLayerPort>[0])} readOnly={false} />)
      for (const [k, v] of Object.entries(rows)) {
        const got = k === 'Mode' || k === 'IPv6' ? radio(k) : val(k)
        expect(got, `${name} ${k}`).toBe(v)
      }
      unmount()
    }
    render(<PortConfigTab port={wireLayerPort('l3_resolved')} readOnly={false} />)
    expect(screen.getByText('resolved')).toBeInTheDocument()   // the ARP result beside Destination IPv4
    cleanup()
    render(<PortConfigTab port={wireLayerPort('l3_unresolved')} readOnly={false} />)
    expect(screen.getByLabelText('Destination IPv4').parentElement).toHaveTextContent('unresolved')
  })

  it('in L3 Destination MAC is read-only and shows the resolved MAC', () => {
    render(<PortConfigTab port={owned('l3_resolved')} readOnly={false} />)
    const mac = screen.getByLabelText('Destination MAC')
    expect(mac).not.toBeInstanceOf(HTMLInputElement)
    expect(mac).toHaveTextContent(PEER)
    expect(screen.getByLabelText('Source MAC')).toHaveTextContent(SRC)
    expect(screen.getByText('set in the server config')).toHaveClass('muted')
  })

  it('in L3 an unresolved Destination MAC says so, with the reason after an Apply', async () => {
    ok({ arp: { state: 'unresolved', mac: null, error: 'Could not resolve 1.1.1.99' } })
    render(<PortConfigTab port={owned('l3_unresolved')} readOnly={false} />)
    expect(screen.getByLabelText('Destination MAC')).toHaveTextContent('unresolved')
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }))
    await screen.findByRole('status')
    expect(screen.getByLabelText('Destination MAC')).toHaveTextContent('unresolved: Could not resolve 1.1.1.99')
  })

  it('switching an L2 port to L3 before Apply does not show its L2 MAC as an ARP result', async () => {
    render(<PortConfigTab port={owned('l2')} readOnly={false} />)
    await userEvent.click(screen.getByLabelText('L3'))
    const mac = screen.getByLabelText('Destination MAC')
    expect(mac).not.toHaveTextContent(PEER)
    expect(mac).toHaveTextContent('resolved after Apply')
  })

  it('the unresolved reason is dropped when the view moves on', async () => {
    ok({ arp: { state: 'unresolved', mac: null, error: 'Could not resolve 1.1.1.99' } })
    const { rerender } = render(<PortConfigTab port={owned('l3_unresolved')} readOnly={false} />)
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }))
    await screen.findByRole('status')
    expect(screen.getByLabelText('Destination MAC')).toHaveTextContent('Could not resolve')
    const moved = owned('l3_unresolved'); moved.layer_cfg = { ...moved.layer_cfg, ipv4: { ...moved.layer_cfg.ipv4, state: 'unresolved', dst: '1.1.1.100' } }
    rerender(<PortConfigTab port={moved} readOnly={false} />)
    expect(screen.getByLabelText('Destination MAC')).toHaveTextContent(/^unresolved$/)
  })

  it('Revert restores the form from the view', async () => {
    render(<PortConfigTab port={owned('l2')} readOnly={false} />)
    const revert = screen.getByRole('button', { name: 'Revert' })
    expect(revert).toBeDisabled()
    expect(screen.queryByText('Unapplied changes')).toBeNull()
    const mac = screen.getByLabelText('Destination MAC')
    await userEvent.clear(mac); await userEvent.type(mac, 'aa:aa:aa:aa:aa:aa')
    await userEvent.type(screen.getByLabelText('Outer VLAN'), '7')
    expect(screen.getByText('Unapplied changes')).toBeInTheDocument()
    await userEvent.click(revert)
    expect(screen.getByLabelText('Destination MAC')).toHaveValue(PEER)
    expect(screen.getByLabelText('Outer VLAN')).toHaveValue('')
    expect(screen.queryByText('Unapplied changes')).toBeNull()
  })

  it('Ping and the neighbour scan are not on Config', () => {
    render(<PortConfigTab port={owned('l2')} readOnly={false} />)
    expect(screen.queryByRole('heading', { name: 'Ping' })).toBeNull()
    expect(screen.queryByRole('heading', { name: 'IPv6 neighbours' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Ping' })).toBeNull()
    expect(screen.queryByRole('button', { name: /scan/i })).toBeNull()
  })

  it('seeds the form from the view', () => {
    render(<PortConfigTab port={owned('vlan_two_tags')} readOnly={false} />)
    expect(screen.getByLabelText('Destination MAC')).toHaveValue(PEER)
    expect(screen.getByLabelText('Outer VLAN')).toHaveValue('10')
    expect(screen.getByLabelText('Inner VLAN')).toHaveValue('20')
  })

  it('L2 Apply sends dst_mac lower-cased', async () => {
    const spy = ok()
    render(<PortConfigTab port={owned('l2')} readOnly={false} />)
    const mac = screen.getByLabelText('Destination MAC')
    await userEvent.clear(mac); await userEvent.type(mac, 'AA:BB:CC:00:11:22')
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }))
    expect(spy).toHaveBeenCalledWith({ port: 0, mode: 'L2', vlan: [], dst_mac: 'aa:bb:cc:00:11:22' })
    expect(await screen.findByText('Applied')).toBeInTheDocument()
  })

  it('L3 Apply sends both IPv4 addresses and the VLANs outer first', async () => {
    const spy = ok()
    render(<PortConfigTab port={owned('l3_resolved')} readOnly={false} />)
    await userEvent.type(screen.getByLabelText('Outer VLAN'), '7')
    await userEvent.type(screen.getByLabelText('Inner VLAN'), '8')
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }))
    expect(spy).toHaveBeenCalledWith(expect.objectContaining({ mode: 'L3', src_ipv4: '1.1.1.1', dst_ipv4: '2.2.2.2', vlan: [7, 8] }))
  })

  it('a VLAN of 4096 is rejected inline and there is no third VLAN field', async () => {
    const spy = ok()
    render(<PortConfigTab port={owned('l2')} readOnly={false} />)
    await userEvent.type(screen.getByLabelText('Outer VLAN'), '4096')
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }))
    expect(screen.getByRole('alert')).toHaveTextContent('from 1 to 4095')
    expect(spy).not.toHaveBeenCalled()
    expect(screen.getAllByLabelText(/VLAN/)).toHaveLength(2)
  })

  it('an IPv6 destination in L3 is rejected inline', async () => {
    const spy = ok()
    render(<PortConfigTab port={owned('l3_resolved')} readOnly={false} />)
    const dst = screen.getByLabelText('Destination IPv4')
    await userEvent.clear(dst); await userEvent.type(dst, 'fe80::1')
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Source and destination must both be IPv4')
    expect(spy).not.toHaveBeenCalled()
  })

  it('IPv6 address and auto are sent on a linux_based port', async () => {
    const spy = ok()
    render(<PortConfigTab port={owned('ipv6_address')} readOnly={false} />)
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }))
    expect(spy).toHaveBeenCalledWith(expect.objectContaining({ ipv6: { enabled: true, src: 'fe80::5052:18ff:fe89:91ea' } }))
    await userEvent.click(screen.getByLabelText('Auto'))
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }))
    expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ ipv6: { enabled: true } }))
  })

  it('on the legacy stack the IPv6 controls are disabled with the backend text and no ipv6 is sent', async () => {
    const spy = ok()
    render(<PortConfigTab port={owned('l2')} readOnly={false} />)
    const text = 'IPv6 configuration needs the linux_based stack; this server runs legacy'
    expect(screen.getByText(text)).toBeInTheDocument()
    for (const n of ['Off', 'Auto', 'Address']) expect(screen.getByLabelText(n)).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }))
    expect(spy.mock.calls[0][0]).not.toHaveProperty('ipv6')
  })

  it('a stack override to legacy disables a linux_based fixture', () => {
    const p = owned('ipv6_auto'); p.info = { ...p.info, stack: 'legacy' }
    render(<PortConfigTab port={p} readOnly={false} />)
    expect(screen.getByLabelText('Auto')).toBeDisabled()
  })

  it('unresolved shows its reason', async () => {
    ok({ arp: { state: 'unresolved', mac: null, error: 'Could not resolve 1.1.1.99' } })
    render(<PortConfigTab port={owned('l3_unresolved')} readOnly={false} />)
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }))
    expect(await screen.findByRole('status')).toHaveTextContent('L3 set, ARP unresolved: Could not resolve 1.1.1.99')
  })

  it('shows the server error', async () => {
    vi.spyOn(api, 'applyLayer').mockRejectedValue(new Error('Port 0 is transmitting; stop traffic first.'))
    render(<PortConfigTab port={owned('l2')} readOnly={false} />)
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Port 0 is transmitting; stop traffic first.')
  })

  it('a restore error is shown', async () => {
    ok({ service_restore_error: 'an active capture on port 0 exists' })
    render(<PortConfigTab port={owned('l2')} readOnly={false} />)
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Service mode could not be restored: an active capture on port 0 exists')
    expect(screen.getByText('Applied')).toBeInTheDocument()
  })

  describe('Apply is disabled with its reason', () => {
    const apply = () => screen.getByRole('button', { name: 'Apply' })
    it('when not connected, read-only or not owned', () => {
      const { rerender } = render(<PortConfigTab port={owned('l2')} readOnly={false} connected={false} />)
      expect(apply()).toBeDisabled(); expect(apply()).toHaveAttribute('title', 'not connected')
      expect(screen.getByText('Not connected.')).toBeInTheDocument()   // also shown as text beside Apply
      rerender(<PortConfigTab port={owned('l2')} readOnly />)
      expect(apply()).toHaveAttribute('title', 'read-only session')
      rerender(<PortConfigTab port={owned('l2', { owned: false })} readOnly={false} />)
      expect(apply()).toHaveAttribute('title', 'acquire the port first')
      rerender(<PortConfigTab port={owned('l2')} readOnly={false} selection={{ kind: 'profile', port: 0, profile: 'p2' }} />)
      expect(apply()).toHaveAttribute('title', 'select the default profile')
    })
    it('when transmitting', () => {
      render(<PortConfigTab port={wirePort('tx_owned', { layer_cfg: owned('l2').layer_cfg })} readOnly={false} />)
      expect(apply()).toBeDisabled(); expect(apply()).toHaveAttribute('title', 'transmitting: stop traffic first')
      expect(screen.getByText('Transmitting: stop traffic first.')).toBeInTheDocument()
    })
    it.each([['ping', 'pinging'], ['scan', 'scanning'], ['apply', 'applying']] as const)('when busy with %s', (kind, word) => {
      busy = { 0: kind }
      render(<PortConfigTab port={owned('l2')} readOnly={false} />)
      expect(apply()).toBeDisabled(); expect(apply()).toHaveAttribute('title', `busy: ${word}`)
    })
    it('in filtered service mode', () => {
      render(<PortConfigTab port={owned('l2', { service_filtered: true })} readOnly={false} />)
      expect(apply()).toBeDisabled()
      expect(apply()).toHaveAttribute('title', 'filtered service mode: set it on or off in the Control tab')
    })
  })

  it('a poll change while editing shows Reload and keeps the input', async () => {
    const { rerender } = render(<PortConfigTab port={owned('l2')} readOnly={false} />)
    const mac = screen.getByLabelText('Destination MAC')
    await userEvent.clear(mac); await userEvent.type(mac, 'aa:aa:aa:aa:aa:aa')
    const other = owned('l2'); other.layer_cfg = { ...other.layer_cfg, ether: { src: SRC, dst: '02:00:00:00:00:09', state: 'configured' } }
    rerender(<PortConfigTab port={other} readOnly={false} />)
    expect(screen.getByRole('status')).toHaveTextContent('Changed on the server')
    expect(screen.getByLabelText('Destination MAC')).toHaveValue('aa:aa:aa:aa:aa:aa')
    await userEvent.click(screen.getByRole('button', { name: 'Reload' }))
    expect(screen.getByLabelText('Destination MAC')).toHaveValue('02:00:00:00:00:09')
    expect(screen.queryByText(/Changed on the server/)).toBeNull()
  })

  describe('after the user\'s own Apply', () => {
    const MAC2 = '02:00:00:00:00:09'
    const applied = () => { const p = owned('l2'); p.layer_cfg = { ...p.layer_cfg, ether: { src: SRC, dst: MAC2, state: 'configured' } }; return p }
    const doApply = async () => {
      ok({ ports: [applied()] })
      const view = render(<PortConfigTab port={owned('l2')} readOnly={false} />)
      const mac = screen.getByLabelText('Destination MAC')
      await userEvent.clear(mac); await userEvent.type(mac, MAC2)
      await userEvent.click(screen.getByRole('button', { name: 'Apply' }))
      await screen.findByText('Applied')
      return view
    }
    it('a still-old polled prop does not overwrite the form', async () => {
      const { rerender } = await doApply()
      rerender(<PortConfigTab port={owned('l2')} readOnly={false} />)
      expect(screen.getByLabelText('Destination MAC')).toHaveValue(MAC2)
      rerender(<PortConfigTab port={applied()} readOnly={false} />)
      expect(screen.getByLabelText('Destination MAC')).toHaveValue(MAC2)
      expect(screen.queryByText(/Changed on the server/)).toBeNull()
    })
    it('a still-old prop does not raise Changed on the server over a further edit', async () => {
      const { rerender } = await doApply()
      const mac = screen.getByLabelText('Destination MAC')
      await userEvent.clear(mac); await userEvent.type(mac, 'aa:aa:aa:aa:aa:aa')
      rerender(<PortConfigTab port={owned('l2')} readOnly={false} />)
      expect(screen.queryByText(/Changed on the server/)).toBeNull()
      expect(screen.getByLabelText('Destination MAC')).toHaveValue('aa:aa:aa:aa:aa:aa')
    })
    it('a later genuinely different server value still raises Changed on the server', async () => {
      const { rerender } = await doApply()
      rerender(<PortConfigTab port={applied()} readOnly={false} />)
      const mac = screen.getByLabelText('Destination MAC')
      await userEvent.clear(mac); await userEvent.type(mac, 'aa:aa:aa:aa:aa:aa')
      const other = owned('l2'); other.layer_cfg = { ...other.layer_cfg, ether: { src: SRC, dst: '02:00:00:00:00:0a', state: 'configured' } }
      rerender(<PortConfigTab port={other} readOnly={false} />)
      expect(screen.getByText(/Changed on the server/)).toBeInTheDocument()
    })
  })

  it('an untouched form follows a poll change silently', () => {
    const { rerender } = render(<PortConfigTab port={owned('l2')} readOnly={false} />)
    const other = owned('l2'); other.layer_cfg = { ...other.layer_cfg, ether: { src: SRC, dst: '02:00:00:00:00:09', state: 'configured' } }
    rerender(<PortConfigTab port={other} readOnly={false} />)
    expect(screen.getByLabelText('Destination MAC')).toHaveValue('02:00:00:00:00:09')
    expect(screen.queryByText(/Changed on the server/)).toBeNull()
  })
})
