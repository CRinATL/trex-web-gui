import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import type { PortView } from '../api'
import type { Selection } from '../portActions'
import { pgidStore } from '../pgidStats'
import { statsStore } from '../stats'
import { trafficStore } from '../trafficStore'
import { wirePgid } from '../testPgid'
import { wirePort, wireProfiles } from '../testWire'
import { Toolbar } from './Toolbar'

const port = (id: number, o: Partial<PortView> = {}): PortView => ({
  id, state: 'IDLE', owner: '', owned: false, service: false, service_filtered: false, link_up: true, speed_gbps: 10,
  promiscuous: false, multicast: false, fc_mode: 0, vlan: [], layer_cfg: {}, rx_filter_mode: null, profiles: wireProfiles(o.state), rx_info: {}, info: {}, ...o,
})
beforeEach(() => { vi.restoreAllMocks(); trafficStore.reset() })

function setup(selection: Selection, ports: PortView[], readOnly = false, connected = true, statsConnected = connected) {
  const h = { onError: vi.fn(), onFormError: vi.fn(), onStartAll: vi.fn() }
  render(<Toolbar ports={ports} selection={selection} readOnly={readOnly} connected={connected} statsConnected={statsConnected} {...h} />)
  return h
}
const enabled = (name: string) => !(screen.getByRole('button', { name }) as HTMLButtonElement).disabled

describe('Toolbar', () => {
  it('follows the enabling rules for the selected port', () => {
    setup({ kind: 'port', id: 0 }, [wirePort('tx_owned')])
    // a transmitting port's Start is lit and reads its state (RC1-20)
    expect(['Transmitting', 'Stop', 'Pause', 'Release', 'Stop all', 'Start all', 'Acquire all', 'Release all'].map(enabled)).toEqual([false, true, true, true, true, false, true, true])
    expect(screen.getByRole('button', { name: 'Transmitting' })).toHaveAttribute('data-on')
    expect(screen.getByRole('button', { name: 'Stop' })).toHaveAttribute('data-run', 'stop')
    expect(screen.getByRole('button', { name: 'Pause' })).toHaveAttribute('data-run', 'hold')
    expect(screen.getByRole('button', { name: 'Release' })).not.toHaveAttribute('data-run')
  })
  it('Re-acquire my ports shows, green, only while another session of this user holds a port (RC1-25)', async () => {
    const spy = vi.spyOn(api, 'reacquire').mockResolvedValue({ reacquired: [0], ports: [] })
    const { unmount } = render(<Toolbar ports={[port(0), port(1)]} selection={{ kind: 'root' }} readOnly={false} connected onError={vi.fn()} />)
    expect(screen.queryByRole('button', { name: 'Re-acquire my ports' })).toBeNull()
    unmount()
    setup({ kind: 'root' }, [port(0, { owner: 'me', mine_elsewhere: true }), port(1)])
    const b = screen.getByRole('button', { name: 'Re-acquire my ports' })
    expect(within(screen.getByRole('group', { name: 'All ports' })).getByRole('button', { name: 'Re-acquire my ports' })).toBe(b)
    expect(b).toHaveAttribute('data-run', 'go')
    expect(b).toHaveAttribute('title', 'Port 0 is held under your user name by another session')
    await userEvent.click(b)
    expect(spy).toHaveBeenCalledOnce()
  })
  it('the lit slot says what the port is doing, not what Start would send (review S1)', () => {
    setup({ kind: 'port', id: 0 }, [wirePort('tx_owned')])
    expect(screen.getByRole('button', { name: 'Transmitting' })).toHaveAttribute('title', 'Port 0 is transmitting')
  })
  it('a foreign profile transmitting leaves the default profile\'s Start unlit and enabled (review S2)', () => {
    setup({ kind: 'port', id: 0 }, [port(0, { owned: true, state: 'TX', profiles: { _: 'STREAMS', bench: 'TX' } })])
    const start = screen.getByRole('button', { name: 'Start' })
    expect(start).toBeEnabled()
    expect(start).not.toHaveAttribute('data-on')
  })
  it('a paused port lights the Start slot amber as Paused, and Resume is a go button', () => {
    setup({ kind: 'port', id: 0 }, [wirePort('paused_owned')])
    const lit = screen.getByRole('button', { name: 'Paused' })
    expect(lit).toHaveAttribute('data-run', 'hold')
    expect(lit).toHaveAttribute('data-on')
    expect(screen.getByRole('button', { name: 'Resume' })).toHaveAttribute('data-run', 'go')
  })
  it('the Pause button becomes Resume on a paused port', () => {
    setup({ kind: 'port', id: 0 }, [wirePort('paused_owned')])
    expect(enabled('Resume')).toBe(true)
    expect(screen.queryByRole('button', { name: 'Pause' })).toBeNull()
  })
  it('with _ paused and bench transmitting, Resume is offered and enabled', () => {
    setup({ kind: 'port', id: 0 }, [port(0, { owned: true, state: 'TX', profiles: { _: 'PAUSE', bench: 'TX' } })])
    expect(enabled('Resume')).toBe(true)
    expect(screen.queryByRole('button', { name: 'Pause' })).toBeNull()
  })
  it.each([['TX'], ['STREAMS']])('a foreign profile alone in %s: Start, Pause and Start all are disabled', (st) => {
    setup({ kind: 'port', id: 0 }, [port(0, { owned: true, state: st, profiles: { bench: st } })])
    expect(['Start', 'Pause', 'Start all'].map(enabled)).toEqual([false, false, false])
  })
  it('a foreign profile alone in PAUSE: Resume is not offered', () => {
    setup({ kind: 'port', id: 0 }, [port(0, { owned: true, state: 'PAUSE', profiles: { bench: 'PAUSE' } })])
    expect(screen.queryByRole('button', { name: 'Resume' })).toBeNull()
    expect(enabled('Pause')).toBe(false)
  })
  it('Start starts; Start all enables on an owned STREAMS port', async () => {
    const start = vi.spyOn(api, 'start').mockResolvedValue({ ports: [] })
    const h = setup({ kind: 'port', id: 0 }, [port(0, { owned: true, state: 'STREAMS' })])
    await userEvent.click(screen.getByRole('button', { name: 'Start' }))
    expect(start).toHaveBeenCalledTimes(1)
    expect(h.onError).toHaveBeenLastCalledWith(null)
    await userEvent.click(screen.getByRole('button', { name: 'Start all' }))
    expect(h.onStartAll).toHaveBeenCalledTimes(1)
    expect(start).toHaveBeenCalledTimes(1)
  })
  it('toolbar Start on an untouched form starts at x1 with no limit', async () => {
    const start = vi.spyOn(api, 'start').mockResolvedValue({ ports: [] })
    setup({ kind: 'port', id: 0 }, [port(0, { owned: true, state: 'STREAMS' })])
    await userEvent.click(screen.getByRole('button', { name: 'Start' }))
    expect(start).toHaveBeenCalledWith({ ports: [0], mult: '1', duration: -1 })
  })
  it('toolbar Start with a bad form reports onFormError and calls nothing', async () => {
    const start = vi.spyOn(api, 'start')
    trafficStore.set(0, { multValue: '0' })
    const h = setup({ kind: 'port', id: 0 }, [port(0, { owned: true, state: 'STREAMS' })])
    await userEvent.click(screen.getByRole('button', { name: 'Start' }))
    expect(h.onFormError).toHaveBeenCalledWith(0, 'Enter a multiplier greater than 0')
    expect(start).not.toHaveBeenCalled()
    expect(h.onError).not.toHaveBeenCalled()
  })
  it('a start rejection reaches onError, not onFormError', async () => {
    vi.spyOn(api, 'start').mockRejectedValue(new api.ApiError(409, 'x', 'busy'))
    const h = setup({ kind: 'port', id: 0 }, [port(0, { owned: true, state: 'STREAMS' })])
    await userEvent.click(screen.getByRole('button', { name: 'Start' }))
    expect(h.onError).toHaveBeenLastCalledWith('busy')
    expect(h.onFormError).not.toHaveBeenCalled()
  })
  it("Start's title says what it will send", () => {
    trafficStore.set(0, { multValue: '5', unit: 'kpps' })
    const { unmount } = render(<Toolbar ports={[port(0, { owned: true, state: 'STREAMS' })]} selection={{ kind: 'port', id: 0 }} readOnly={false} connected onError={() => undefined} />)
    expect(screen.getByRole('button', { name: 'Start' })).toHaveAttribute('title', 'Start at 5 kpps, no limit')
    unmount()
    trafficStore.set(0, { multValue: '1', unit: 'x', duration: '30', force: true })
    const second = render(<Toolbar ports={[port(0, { owned: true, state: 'STREAMS' })]} selection={{ kind: 'port', id: 0 }} readOnly={false} connected onError={() => undefined} />)
    expect(screen.getByRole('button', { name: 'Start' })).toHaveAttribute('title', 'Start at ×1, 30 s, force')
    second.unmount()
    trafficStore.set(0, { multValue: '' })
    render(<Toolbar ports={[port(0, { owned: true, state: 'STREAMS' })]} selection={{ kind: 'port', id: 0 }} readOnly={false} connected onError={() => undefined} />)
    expect(screen.getByRole('button', { name: 'Start' })).toHaveAttribute('title', 'Enter a multiplier greater than 0')
  })
  it('a port selection labels the group Port N; the root keeps it in place, labelled Port —, with every button disabled', () => {
    const { unmount } = render(<Toolbar ports={[port(0)]} selection={{ kind: 'port', id: 0 }} readOnly={false} connected onError={() => undefined} />)
    expect(within(screen.getByRole('group', { name: 'Port 0' })).getByText('Port 0')).toBeVisible()
    unmount()
    render(<Toolbar ports={[port(0)]} selection={{ kind: 'root' }} readOnly={false} connected onError={() => undefined} />)
    const g = screen.getByRole('group', { name: 'Port —' })
    expect(within(g).getByText('Port —')).toBeVisible()
    const buttons = within(g).getAllByRole('button')
    // the shown label: a run button also holds its other labels, hidden, to keep its width
    const shown = (b: HTMLElement) => b.querySelector('.run-label > :first-child')?.textContent ?? b.textContent
    expect(buttons.map(shown)).toEqual(['Start', 'Pause', 'Stop', 'Acquire'])
    for (const b of buttons) { expect(b).toBeDisabled(); expect(b).toHaveAttribute('title', 'Select a port') }
  })
  it("Start's title shows the trimmed values it will send", () => {
    trafficStore.set(0, { multValue: ' 5 ', unit: 'kpps', duration: ' 30 ' })
    setup({ kind: 'port', id: 0 }, [port(0, { owned: true, state: 'STREAMS' })])
    expect(screen.getByRole('button', { name: 'Start' })).toHaveAttribute('title', 'Start at 5 kpps, 30 s')
  })
  it('read-only with a port selected disables every Port group button', () => {
    setup({ kind: 'port', id: 0 }, [port(0, { owned: true, state: 'TX', profiles: { _: 'TX' } })], true)
    for (const b of within(screen.getByRole('group', { name: 'Port 0' })).getAllByRole('button')) expect(b).toBeDisabled()
  })
  it('the All ports group has Start all, Stop all, Acquire all, Release all', () => {
    setup({ kind: 'root' }, [port(0)])
    expect(within(screen.getByRole('group', { name: 'All ports' })).getAllByRole('button').map((b) => b.textContent)).toEqual(['Start all', 'Stop all', 'Acquire all', 'Release all'])
  })
  it('Pause, Resume and Stop call the API', async () => {
    const pause = vi.spyOn(api, 'pause').mockResolvedValue({ ports: [] })
    const resume = vi.spyOn(api, 'resume').mockResolvedValue({ ports: [] })
    const stop = vi.spyOn(api, 'stop').mockResolvedValue({ ports: [] })
    const tx = render(<Toolbar ports={[port(0, { owned: true, state: 'TX', profiles: { _: 'TX' } })]} selection={{ kind: 'port', id: 0 }} readOnly={false} connected onError={() => undefined} />)
    await userEvent.click(screen.getByRole('button', { name: 'Pause' }))
    expect(pause).toHaveBeenCalledWith({ ports: [0] })
    await userEvent.click(screen.getByRole('button', { name: 'Stop' }))
    expect(stop).toHaveBeenCalledWith({ ports: [0] })
    tx.unmount()
    render(<Toolbar ports={[port(0, { owned: true, state: 'PAUSE', profiles: { _: 'PAUSE' } })]} selection={{ kind: 'port', id: 0 }} readOnly={false} connected onError={() => undefined} />)
    await userEvent.click(screen.getByRole('button', { name: 'Resume' }))
    expect(resume).toHaveBeenCalledWith({ ports: [0] })
  })
  it('Pause and Stop drop a pending update', async () => {
    vi.spyOn(api, 'pause').mockResolvedValue({ ports: [] })
    vi.spyOn(api, 'stop').mockResolvedValue({ ports: [] })
    const pend = { mult: '20kpps', left: 5 }
    trafficStore.set(0, { pending: pend })
    setup({ kind: 'port', id: 0 }, [port(0, { owned: true, state: 'TX', profiles: { _: 'TX' } })])
    await userEvent.click(screen.getByRole('button', { name: 'Pause' }))
    expect(trafficStore.get(0).pending).toBeNull()
    trafficStore.set(0, { pending: pend })
    await userEvent.click(screen.getByRole('button', { name: 'Stop' }))
    expect(trafficStore.get(0).pending).toBeNull()
  })
  it('Stop all drops every pending update', async () => {
    vi.spyOn(api, 'stop').mockResolvedValue({ ports: [] })
    const pend = { mult: '20kpps', left: 5 }
    trafficStore.set(0, { pending: pend }); trafficStore.set(1, { pending: pend })
    setup({ kind: 'root' }, [port(0, { owned: true, state: 'TX', profiles: { _: 'TX' } }), port(1, { owned: true, state: 'TX', profiles: { _: 'TX' } })])
    await userEvent.click(screen.getByRole('button', { name: 'Stop all' }))
    expect(trafficStore.get(0).pending).toBeNull(); expect(trafficStore.get(1).pending).toBeNull()
  })
  it('read-only disables every command', () => {
    setup({ kind: 'root' }, [port(0, { owned: true, state: 'TX' })], true)
    for (const n of ['Start all', 'Stop all', 'Acquire all', 'Release all']) expect(enabled(n)).toBe(false)
  })
  it('Acquire all acquires every port; a rejection reaches onError', async () => {
    const spy = vi.spyOn(api, 'acquire').mockRejectedValue(new api.ApiError(409, 'x', 'owned by bob'))
    const h = setup({ kind: 'root' }, [port(0), port(1)])
    await userEvent.click(screen.getByRole('button', { name: 'Acquire all' }))
    expect(spy).toHaveBeenCalledWith({ ports: [0, 1] })
    expect(h.onError).toHaveBeenLastCalledWith('owned by bob')
  })
  it('a successful command reports null so the alert clears', async () => {
    vi.spyOn(api, 'stop').mockResolvedValue({ ports: [] })
    const h = setup({ kind: 'port', id: 0 }, [port(0, { owned: true, state: 'TX' })])
    await userEvent.click(screen.getByRole('button', { name: 'Stop' }))
    expect(h.onError).toHaveBeenLastCalledWith(null)
  })
  it('a double click on a command sends one request', async () => {
    let done!: (v: { ports: PortView[] }) => void
    const spy = vi.spyOn(api, 'stop').mockReturnValue(new Promise((r) => { done = r }))
    setup({ kind: 'port', id: 0 }, [port(0, { owned: true, state: 'TX' })])
    await userEvent.dblClick(screen.getByRole('button', { name: 'Stop' }))
    expect(spy).toHaveBeenCalledTimes(1)
    done({ ports: [] })
  })
  it('has no Connect…, Disconnect or Dashboard button', () => {
    for (const connected of [true, false]) {
      const { unmount } = render(<Toolbar ports={[]} selection={{ kind: 'root' }} readOnly={false} connected={connected} onError={() => undefined} />)
      for (const n of ['Connect…', 'Disconnect', 'Dashboard']) expect(screen.queryByRole('button', { name: n })).toBeNull()
      unmount()
    }
  })
  it('Clear stats calls statsStore.clear, and nothing else: no fetch, no api call', async () => {
    const clear = vi.spyOn(statsStore, 'clear')
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    const stop = vi.spyOn(api, 'stop'); const release = vi.spyOn(api, 'release')
    setup({ kind: 'root' }, [wirePort('tx_owned')])
    await userEvent.click(screen.getByRole('button', { name: 'Clear stats' }))
    expect(clear).toHaveBeenCalledTimes(1)
    expect(fetchSpy).not.toHaveBeenCalled()
    expect(stop).not.toHaveBeenCalled(); expect(release).not.toHaveBeenCalled()
  })
  it('Clear stats says in its tooltip that it resets only what this browser shows', () => {
    setup({ kind: 'root' }, [wirePort('tx_owned')])
    expect(screen.getByRole('button', { name: 'Clear stats' })).toHaveAttribute('title', 'Resets the counters shown in this browser; nothing is sent to TRex')
  })
  it('Clear stats also clears the PGID counters', async () => {
    pgidStore.reset()
    pgidStore.push(wirePgid('tx'))
    expect(pgidStore.relative(5, 'tx_pkts', 'total')).toBe(90909)
    setup({ kind: 'root' }, [wirePort('tx_owned')])
    await userEvent.click(screen.getByRole('button', { name: 'Clear stats' }))
    expect(pgidStore.relative(5, 'tx_pkts', 'total')).toBe(0)
    pgidStore.reset()
  })
  it('Clear stats is disabled while connecting (connected-or-connecting is not enough)', () => {
    setup({ kind: 'root' }, [], false, true, false)
    expect(screen.getByRole('button', { name: 'Clear stats' })).toBeDisabled()
  })
  it('Clear stats is disabled while not connected', () => {
    setup({ kind: 'root' }, [], false, false)
    expect(screen.getByRole('button', { name: 'Clear stats' })).toBeDisabled()
  })
  it('TRex daemon… is enabled while disconnected and calls onDaemon', async () => {
    const onDaemon = vi.fn()
    render(<Toolbar ports={[]} selection={{ kind: 'root' }} readOnly={false} connected={false} onError={() => undefined} onDaemon={onDaemon} />)
    expect(enabled('TRex daemon…')).toBe(true)
    await userEvent.click(screen.getByRole('button', { name: 'TRex daemon…' }))
    expect(onDaemon).toHaveBeenCalledTimes(1)
  })
  it('has no TRex daemon… button unless the page owns the dialog', () => {
    setup({ kind: 'root' }, [])
    expect(screen.queryByRole('button', { name: 'TRex daemon…' })).toBeNull()
  })
})
