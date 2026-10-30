import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import capturePkts from '../../__fixtures__/capturePkts.json'
import fixture from '../../__fixtures__/packetTree.json'
import vlanFixture from '../../__fixtures__/packetTreeVlan.json'   // copied from tests/fixtures/p3c/trees.json (the contract test pins packetTree.json to the `udp` scenario only)
import { PacketViewer } from './PacketViewer'

const row = { ...(capturePkts.batch.rows[0] as api.DecodedRow), binary: fixture.udp.binary }
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('PacketViewer', () => {
  it('opens with the tree and hex of the row', async () => {
    const tree = vi.spyOn(api, 'packetTree').mockResolvedValue(fixture.udp as unknown as api.PacketTree)
    render(<PacketViewer row={row} onClose={() => undefined} />)
    expect(tree).toHaveBeenCalledWith(row.binary)
    expect((await screen.findAllByRole('button', { name: /^dst bytes / })).length).toBe(2)   // Ether and IP
    expect(screen.getAllByRole('button', { name: /^byte \d+: 0x/ })).toHaveLength(60)
    expect(screen.getByText(new RegExp(row.info.slice(0, 20)))).toBeInTheDocument()   // the summary line
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })
  it('clicking a field marks its bytes, and a byte selects its field', async () => {
    vi.spyOn(api, 'packetTree').mockResolvedValue(fixture.udp as unknown as api.PacketTree)
    render(<PacketViewer row={row} onClose={() => undefined} />)
    await userEvent.click(await screen.findByRole('button', { name: /^src bytes 0006-000b/ }))
    expect(screen.getAllByRole('button', { pressed: true, name: /^byte/ })).toHaveLength(6)
    await userEvent.click(screen.getByRole('button', { name: 'byte 12: 0x08' }))   // Ether type, 2 bytes
    expect(screen.getAllByRole('button', { pressed: true, name: /^byte/ }).map((b) => b.getAttribute('aria-label'))).toEqual(['byte 12: 0x08', 'byte 13: 0x00'])
    expect(screen.getByRole('button', { name: /^type bytes 000c-000d/ })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: /^src bytes 0006-000b/ })).toHaveAttribute('aria-pressed', 'false')
  })
  it('an ICMP echo: a byte of the payload selects Raw load, not the absent ICMP fields behind it', async () => {
    const vlan = vlanFixture.vlan as unknown as api.PacketTree
    vi.spyOn(api, 'packetTree').mockResolvedValue(vlan)
    render(<PacketViewer row={{ ...row, binary: vlan.binary }} onClose={() => undefined} />)
    await screen.findByRole('button', { name: /^load bytes / })
    expect(screen.queryByRole('button', { name: /^ts_ori/ })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: /^byte 46: / }))
    const marked = screen.getAllByRole('button', { pressed: true, name: /^byte/ })
    expect(marked[0].getAttribute('aria-label')).toMatch(/^byte 46:/)
    expect(marked).toHaveLength(atob(vlan.binary).length - 46)   // Raw load runs to the end of the frame
    expect(screen.getByRole('button', { name: /^load bytes 002e-/, pressed: true })).toBeInTheDocument()
  })
  it('a byte on the first byte of the UDP header selects sport only, not the empty IP options', async () => {
    vi.spyOn(api, 'packetTree').mockResolvedValue(fixture.udp as unknown as api.PacketTree)
    render(<PacketViewer row={row} onClose={() => undefined} />)
    await screen.findAllByRole('button', { name: /^dst bytes / })
    await userEvent.click(screen.getByRole('button', { name: /^byte 34: / }))
    expect(screen.getAllByRole('button', { pressed: true, name: /^byte/ })).toHaveLength(2)
    expect(screen.getAllByRole('button', { pressed: true }).filter((b) => !/^byte/.test(b.getAttribute('aria-label') ?? ''))).toHaveLength(1)
    expect(screen.getByRole('button', { name: /^sport bytes 0022-0023/, pressed: true })).toBeInTheDocument()
  })
  it('the footer has Copy hex on the left and Close in .end', async () => {
    vi.spyOn(api, 'packetTree').mockResolvedValue(fixture.udp as unknown as api.PacketTree)
    render(<PacketViewer row={row} onClose={() => undefined} />)
    const footer = screen.getByRole('dialog').querySelector('.dialog-footer')!
    expect(footer.querySelector(':scope > button')).toHaveTextContent('Copy hex')
    expect([...footer.querySelectorAll('.end button')].map((b) => b.textContent)).toEqual(['Close'])
    await screen.findAllByRole('button', { name: /^dst bytes / })
  })
  it('Escape closes', async () => {
    vi.spyOn(api, 'packetTree').mockResolvedValue(fixture.udp as unknown as api.PacketTree)
    const onClose = vi.fn()
    render(<PacketViewer row={row} onClose={onClose} />)
    await userEvent.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledTimes(1)
  })
  it('the tree request failing shows the error and the hex still renders', async () => {
    vi.spyOn(api, 'packetTree').mockRejectedValue(new Error('scapy is down'))
    render(<PacketViewer row={row} onClose={() => undefined} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('scapy is down')
    expect(screen.getAllByRole('button', { name: /^byte \d+: 0x/ })).toHaveLength(60)
  })
  it('Copy hex writes the frame as hex', async () => {
    vi.spyOn(api, 'packetTree').mockResolvedValue(fixture.udp as unknown as api.PacketTree)
    const write = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText: write } })
    render(<PacketViewer row={row} onClose={() => undefined} />)
    await userEvent.click(screen.getByRole('button', { name: 'Copy hex' }))
    expect(write).toHaveBeenCalledTimes(1)
    expect(write.mock.calls[0][0]).toMatch(/^52 52 18 89 91 ea 8e 4e/)
  })
  it('viewer mode fetches no definitions: the only request is the capture tree', async () => {
    const fetched: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => {
      fetched.push(String(url))
      return new Response(JSON.stringify(fixture.udp), { status: 200, headers: { 'content-type': 'application/json' } })
    }))
    render(<PacketViewer row={row} onClose={() => undefined} />)
    await screen.findAllByRole('button', { name: /^dst bytes / })
    expect(fetched).toEqual(['/api/capture/packet/tree'])
  })
  it('a row with no addresses shows no arrow', async () => {
    vi.spyOn(api, 'packetTree').mockResolvedValue(fixture.udp as unknown as api.PacketTree)
    const { container, rerender } = render(<PacketViewer row={{ ...row, src: '', dst: '', proto: 'UDP', wirelen: 60, info: '' }} onClose={() => undefined} />)
    expect(container.querySelector('.packet-summary')?.textContent).toBe('UDP 60 bytes')
    await screen.findAllByRole('button', { name: /^dst bytes / })
    rerender(<PacketViewer row={{ ...row, src: '10.0.0.1', dst: '10.0.0.2', proto: 'UDP', wirelen: 60, info: '' }} onClose={() => undefined} />)
    expect(container.querySelector('.packet-summary')?.textContent).toBe('10.0.0.1 > 10.0.0.2 UDP 60 bytes')
  })
})
