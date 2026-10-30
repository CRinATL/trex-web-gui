import { fireEvent, render, screen } from '@testing-library/react'
import { useEffect, useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type * as api from '../api'
import { wireProfiles } from '../testWire'
import { trafficStore } from '../trafficStore'
import { TrafficTab } from './TrafficTab'

// Its own file on purpose: React reports "Cannot update a component while rendering a different component" once per
// process by the rendering component's name, so any earlier TrafficTab warning in a shared file would hide this one.
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers() })

const port = (o: Partial<api.PortView> = {}): api.PortView => ({
  id: 0, state: 'STREAMS', owner: 'me', owned: true, service: false, service_filtered: false, link_up: true, speed_gbps: 10, promiscuous: false,
  multicast: false, fc_mode: 0, vlan: [], layer_cfg: {}, rx_filter_mode: null, profiles: wireProfiles(o.state ?? 'STREAMS'), rx_info: {}, info: {}, ...o,
})

describe('TrafficTab render purity', () => {
  it('hiding the tab during a countdown writes the store from an effect, not from render', async () => {
    vi.useFakeTimers()
    const errors = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    // a subscriber that sets state on notify (an effect-based reader): updating it during another component's render warns
    function Reader() {
      const [v, setV] = useState(trafficStore.get(0).multValue)
      useEffect(() => trafficStore.subscribe(() => setV(trafficStore.get(0).multValue)), [])
      return <span data-testid="r">{v}</span>
    }
    const p = port({ state: 'TX' })
    const el = (visible: boolean) => <><Reader /><TrafficTab port={p} selection={{ kind: 'port', id: 0 }} ports={[p]} readOnly={false} connected visible={visible} /></>
    const { rerender } = render(el(true))
    fireEvent.change(screen.getByLabelText('Multiplier'), { target: { value: '20 kpps' } })
    fireEvent.keyDown(screen.getByLabelText('Multiplier'), { key: 'Enter' })
    expect(screen.getByTestId('r')).toHaveTextContent('20')
    rerender(el(false))
    expect(screen.getByTestId('r')).toHaveTextContent('1')
    expect(errors).not.toHaveBeenCalled()
  })
  it('leaving TX during a countdown writes the store from an effect, not from render', () => {
    vi.useFakeTimers()
    const errors = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    function Reader() {
      const [v, setV] = useState(trafficStore.get(0).pending)
      useEffect(() => trafficStore.subscribe(() => setV(trafficStore.get(0).pending)), [])
      return <span data-testid="p">{v ? v.mult : 'none'}</span>
    }
    const el = (p: api.PortView) => <><Reader /><TrafficTab port={p} selection={{ kind: 'port', id: 0 }} ports={[p]} readOnly={false} connected /></>
    const { rerender } = render(el(port({ state: 'TX' })))
    fireEvent.change(screen.getByLabelText('Multiplier'), { target: { value: '20' } })
    fireEvent.keyDown(screen.getByLabelText('Multiplier'), { key: 'Enter' })
    expect(screen.getByTestId('p')).toHaveTextContent('20')
    rerender(el(port({ state: 'STREAMS' })))
    expect(screen.getByTestId('p')).toHaveTextContent('none')
    expect(trafficStore.get(0).pending).toBeNull()
    expect(errors).not.toHaveBeenCalled()
  })
})
