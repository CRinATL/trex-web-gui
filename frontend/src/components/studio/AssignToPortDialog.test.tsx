import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import type { LibraryEntry, PortView } from '../../api'
import { wireProfiles } from '../../testWire'
import { trafficStore } from '../../trafficStore'
import { AssignToPortDialog } from './AssignToPortDialog'

afterEach(() => { vi.restoreAllMocks(); trafficStore.reset() })
const port = (id: number): PortView => ({ id, state: 'IDLE', owner: 'me', owned: true, service: false, service_filtered: false, link_up: true, speed_gbps: 10, promiscuous: false,
  multicast: false, fc_mode: 0, vlan: [], layer_cfg: {}, rx_filter_mode: null, profiles: wireProfiles('IDLE'), rx_info: {}, info: {} })
const json: LibraryEntry = { slug: 'a', name: 'a', path: '/lib/a.json', stream_count: 1, created: 'c', updated: 'u', source_missing: false, load_error: null,
  source: { kind: 'json', path: null, args: [], tunables: null, ports_differ: false, imported: 'i' } }
const py: LibraryEntry = { ...json, slug: 'p', source: { kind: 'py', path: '/srv/p/udp.py', args: [], tunables: { values: { n: 3 }, extra: '--x 1' }, ports_differ: false, imported: 'i' } }
function Where() { const l = useLocation(); return <p data-testid="where">{l.pathname} {JSON.stringify(l.state)}</p> }
const show = (entry: LibraryEntry) => render(<MemoryRouter initialEntries={['/studio']}><AssignToPortDialog entry={entry} ports={[port(0), port(2)]} onClose={vi.fn()} /><Where /></MemoryRouter>)

describe('AssignToPortDialog', () => {
  it("Open Traffic tab fills the port's form and navigates with the port", async () => {
    const inspected: api.Inspected = { path: '/lib/a.json', kind: 'json', tunables: [], summary: [], stream_count: 1 }
    // resolves on a later timer, as a loaded run does, so the test cannot pass by reading the location too early
    const insp = vi.spyOn(api, 'inspect').mockImplementation(() => new Promise(r => setTimeout(() => r(inspected), 30)))
    const asg = vi.spyOn(api, 'assign')
    show(json)
    expect(screen.getByRole('dialog', { name: 'Use in Control' })).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Port' }), '2')
    await userEvent.click(screen.getByRole('button', { name: 'Open Traffic tab' }))
    // assign() navigates only after inspect resolves; under load that lands after the click returns
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/control {"studioAssign":{"port":2}}'))
    expect(insp).toHaveBeenCalledWith({ path: '/lib/a.json', ports: [2], tunables: [] })
    expect(trafficStore.get(2)).toMatchObject({ path: '/lib/a.json', formPath: '/lib/a.json', inspected })
    expect(trafficStore.get(0).path).toBe('')
    expect(asg).not.toHaveBeenCalled()   // nothing is assigned until the user presses Assign in Control
  })
  it('a .py entry fills the source path and tunables without inspecting', async () => {
    const insp = vi.spyOn(api, 'inspect')
    show(py)
    expect(screen.getByText(/editor changes to this entry are not sent/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Open Traffic tab' }))
    expect(insp).not.toHaveBeenCalled()
    expect(trafficStore.get(0)).toMatchObject({ path: '/srv/p/udp.py', formPath: '/srv/p/udp.py', inspected: null, values: { n: 3 }, extra: '--x 1', libraryPy: '/srv/p/udp.py' })
    expect(screen.getByTestId('where')).toHaveTextContent('/control {"studioAssign":{"port":0}}')
  })
  it("names the rate the target port's form keeps, which the handoff does not change", async () => {
    trafficStore.set(2, { multValue: '5', unit: 'kpps', duration: '30' })
    show(json)
    expect(screen.getByText("Port 0's rate stays ×1: check it on the Traffic tab before you press Assign.")).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Port' }), '2')
    expect(screen.getByText("Port 2's rate stays 5 kpps, 30 s: check it on the Traffic tab before you press Assign.")).toBeInTheDocument()
  })
  it("spans each note across the form grid's two columns, so none is squeezed into the field column", () => {
    show(py)
    const notes = [...document.querySelectorAll('.form-grid > p')]
    expect(notes.map((p) => p.textContent?.slice(0, 20))).toEqual(['Fills the profile fi', "Port 0's rate stays ", expect.any(String)])
    for (const p of notes) expect(p).toHaveClass('full')
  })
  it('an inspect failure stays in the dialog and leaves the form alone', async () => {
    vi.spyOn(api, 'inspect').mockRejectedValue(new Error('bad profile'))
    show(json)
    await userEvent.click(screen.getByRole('button', { name: 'Open Traffic tab' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('bad profile')
    expect(trafficStore.get(0).path).toBe('')
    expect(screen.getByTestId('where')).toHaveTextContent('/studio')
  })
})
