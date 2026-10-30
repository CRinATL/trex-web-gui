import { act, cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import type { BuildResult, Definitions, LibraryEntry, PacketDoc, Source, StreamPage } from '../../api'
import buildFixture from '../../__fixtures__/packet/buildUdp.json'
import defsFixture from '../../__fixtures__/packet/definitions.json'
import library from '../../__fixtures__/studioLibrary.json'
import sim from '../../__fixtures__/studioSim.json'
import streams from '../../__fixtures__/studioStreams.json'
import { ProfileEditor } from './ProfileEditor'

afterEach(() => { cleanup(); vi.restoreAllMocks() })
const py = library.udp_for_benchmarks.profiles[0] as unknown as LibraryEntry
const page = streams.udp_for_benchmarks as unknown as StreamPage
const withSource = (o: Partial<Source>): LibraryEntry => ({ ...py, source: { ...(py.source as Source), ...o } })

function show(entry: LibraryEntry) {
  const get = vi.spyOn(api, 'getStreams').mockResolvedValue(page)
  const onChanged = vi.fn()
  render(<ProfileEditor entry={entry} onChanged={onChanged} />)
  return { get, onChanged, user: userEvent.setup() }
}

describe('ProfileEditor', () => {
  it('a .py entry shows that edits are not what Control assigns', async () => {
    show(py)
    expect(await screen.findByText(/Source: py <fixtures>\/profiles\/udp_for_benchmarks\.py · saved 2026-01-01 00:00:00 UTC · 1 stream$/)).toBeInTheDocument()
    expect(screen.getByText('Control assigns the .py source with its tunables; edits here are not sent.')).toBeInTheDocument()
    expect(screen.queryByText(/port 1's streams differ/)).not.toBeInTheDocument()
    cleanup()
    show(withSource({ kind: 'json', path: '/in/x.json' }))
    expect(await screen.findByText(/Source: json \/in\/x\.json/)).toBeInTheDocument()
    expect(screen.queryByText(/Control assigns/)).not.toBeInTheDocument()
  })

  it('View source… is offered on a .py entry only and opens the source dialog', async () => {
    const src = vi.spyOn(api, 'profileSource').mockResolvedValue({ path: '/in/udp.py', text: 'print(1)\n' })
    const { user } = show(py)
    await user.click(await screen.findByRole('button', { name: 'View source…' }))
    expect(src).toHaveBeenCalledWith('udp-for-benchmarks')
    expect(await screen.findByRole('dialog', { name: 'Profile source' })).toHaveTextContent('print(1)')
    await user.click(screen.getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog', { name: 'Profile source' })).not.toBeInTheDocument()
    cleanup()
    show(withSource({ kind: 'json', path: '/in/x.json' }))
    await screen.findByRole('table', { name: 'Streams' })
    expect(screen.queryByRole('button', { name: 'View source…' })).not.toBeInTheDocument()
  })

  it('a properties Save is one undo step', async () => {
    const { user } = show(py)
    const put = vi.spyOn(api, 'putStreams').mockResolvedValue({ rev: 'r1', stream_count: 1, renamed: {}, cleared: [], warnings: [] })
    await user.click(await screen.findByRole('cell', { name: /UDP/ }))
    const isg = await screen.findByLabelText(/^ISG/)
    await user.clear(isg)
    await user.type(isg, '7')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(put).toHaveBeenCalledTimes(1)
    const before = page.rows[0].stream.isg
    await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Undo' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Undo' }))
    expect(put).toHaveBeenLastCalledWith('udp-for-benchmarks', { rev: 'r1', changed: { 0: expect.objectContaining({ isg: before }) } })
  })

  it('a properties Save behind a pending write leaves no undo entry', async () => {
    const { user } = show(py)
    let answer: (r: api.DiffReply) => void = () => undefined
    const put = vi.spyOn(api, 'putStreams').mockImplementationOnce(() => new Promise((resolve) => { answer = resolve }))
    await user.click(await screen.findByRole('checkbox', { name: 'Enabled #0' }))
    await user.click(await screen.findByRole('cell', { name: /UDP/ }))
    const isg = await screen.findByLabelText(/^ISG/)
    await user.clear(isg)
    await user.type(isg, '7')
    put.mockResolvedValueOnce({ rev: 'r2', stream_count: 1, renamed: {}, cleared: [], warnings: [] })
    await user.click(screen.getByRole('button', { name: 'Save' }))
    answer({ rev: 'r1', stream_count: 1, renamed: {}, cleared: [], warnings: [] })
    await vi.waitFor(() => expect(put).toHaveBeenCalledTimes(2))
    await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Undo' })).toBeDisabled())
    expect(screen.getByRole('button', { name: 'Undo' })).not.toHaveAttribute('title')
  })

  it('ports_differ is shown', async () => {
    show(withSource({ ports_differ: true }))
    expect(await screen.findByText("Imported for port 0; port 1's streams differ.")).toBeInTheDocument()
  })

  it('a gone writer offers Reload library', async () => {
    const { onChanged, user } = show(py)
    vi.spyOn(api, 'putStreams').mockRejectedValue(new api.ApiError(404, 'not_found', 'No profile udp-for-benchmarks'))
    await user.click(await screen.findByRole('checkbox', { name: 'Enabled #0' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('This profile was renamed or deleted elsewhere.')
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(screen.getByRole('button', { name: 'Add stream' })).toBeDisabled()
    onChanged.mockClear()
    await user.click(screen.getByRole('button', { name: 'Reload library' }))
    expect(onChanged).toHaveBeenCalledTimes(1)
  })

  it('a stale writer offers Reload, which reads the profile again and writes from its rev', async () => {
    const { get, user } = show(py)
    const put = vi.spyOn(api, 'putStreams').mockRejectedValueOnce(new api.ApiError(409, 'stale', 'The profile changed', { rev: 'newer' }))
    await user.click(await screen.findByRole('checkbox', { name: 'Enabled #0' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('This profile changed elsewhere.')
    get.mockResolvedValue({ ...page, rev: 'newer' })
    await user.click(screen.getByRole('button', { name: 'Reload' }))
    await vi.waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
    put.mockResolvedValue({ rev: 'r2', stream_count: 1, renamed: {}, cleared: [], warnings: [] })
    await user.click(screen.getByRole('checkbox', { name: 'Enabled #0' }))
    expect(put).toHaveBeenLastCalledWith('udp-for-benchmarks', { rev: 'newer', changed: { 0: { enabled: false } } })
  })

  it('an Add stream from before Reload does not free a newer shifting write\'s lock (final review I2)', async () => {
    const { get, user } = show(py)
    let refuseToggle: (e: unknown) => void = () => undefined, answerDelete: (r: api.DiffReply) => void = () => undefined
    const put = vi.spyOn(api, 'putStreams')
      .mockImplementationOnce(() => new Promise((_, reject) => { refuseToggle = reject }))
      .mockImplementationOnce(() => new Promise((resolve) => { answerDelete = resolve }))
    let giveTemplate: (s: api.StreamJson) => void = () => undefined
    vi.spyOn(api, 'getTemplate').mockImplementation(() => new Promise((resolve) => { giveTemplate = resolve }))
    await user.click(await screen.findByRole('checkbox', { name: 'Enabled #0' }))   // in flight; refused stale below
    await user.click(screen.getByRole('button', { name: 'Add stream' }))           // 1. the template request is held
    refuseToggle(new api.ApiError(409, 'stale', 'The profile changed', { rev: 'newer' }))
    get.mockResolvedValue({ ...page, rev: 'newer' })
    await user.click(await screen.findByRole('button', { name: 'Reload' }))        // 2. a new writer
    await vi.waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
    await user.click(await screen.findByRole('cell', { name: '0' }))               // 3. a new shifting write, held
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await user.click(screen.getByRole('button', { name: 'Confirm delete' }))
    expect(put).toHaveBeenCalledTimes(2)
    for (const box of screen.getAllByRole('checkbox')) expect(box).toBeDisabled()  // the delete holds the lock
    const reads = get.mock.calls.length
    await act(async () => {                                                        // 4. the old Add stream goes on
      giveTemplate(page.rows[0].stream)
      await new Promise((r) => setTimeout(r, 20))
    })
    expect(screen.getByRole('button', { name: 'Add stream' })).toBeDisabled()      // 5. still locked
    for (const box of screen.getAllByRole('checkbox')) expect(box).toBeDisabled()
    expect(put).toHaveBeenCalledTimes(2)                                           // the old writer sent nothing
    get.mockResolvedValue({ ...page, rev: 'r2', stream_count: 0, rows: [] })
    answerDelete({ rev: 'r2', stream_count: 0, renamed: {}, cleared: [], warnings: [] })
    await vi.waitFor(() => expect(get.mock.calls.length).toBeGreaterThan(reads))
    await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Add stream' })).toBeEnabled())
  })

  it('a refused write shows its message as the one error line', async () => {
    const { user } = show(py)
    vi.spyOn(api, 'putStreams').mockRejectedValue(new api.ApiError(422, 'invalid_stream', 'stream 0 name: taken', { index: 0, field: 'name' }))
    await user.click(await screen.findByRole('checkbox', { name: 'Enabled #0' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('stream 0 name: taken')
    expect(screen.getByRole('button', { name: 'Add stream' })).toBeEnabled()
  })
  // A delete of stream 0 on a two-page profile, with its PUT held: `go()` answers it.
  async function deleteInFlight() {
    const rows = Array.from({ length: 100 }, (_, i) => ({ ...page.rows[0], index: i, stream: { ...page.rows[0].stream, name: `s${i}` } }))
    const first: StreamPage = { ...page, stream_count: 150, names: [], rows }
    const get = vi.spyOn(api, 'getStreams').mockResolvedValueOnce(first)
    let answer: (r: api.DiffReply) => void = () => undefined
    const put = vi.spyOn(api, 'putStreams').mockImplementationOnce(() => new Promise((resolve) => { answer = resolve }))
    const onChanged = vi.fn()
    render(<ProfileEditor entry={py} onChanged={onChanged} />)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('cell', { name: 's0' }))
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await user.click(screen.getByRole('button', { name: 'Confirm delete' }))
    expect(put).toHaveBeenCalledTimes(1)
    const go = () => answer({ rev: 'r1', stream_count: 149, renamed: {}, cleared: [], warnings: [] })
    return { get, onChanged, user, first, go }
  }
  const held = () => {
    let land: (p: StreamPage) => void = () => undefined, fail: (e: unknown) => void = () => undefined
    const p = new Promise<StreamPage>((resolve, reject) => { land = resolve; fail = reject })
    return { p, land: (x: StreamPage) => land(x), fail: (e: unknown) => fail(e) }
  }

  it('a page read started before a shifting write lands does not end the lock', async () => {
    const { get, onChanged, user, first, go } = await deleteInFlight()
    const early = held(), fresh = held()
    get.mockImplementationOnce(() => early.p).mockImplementationOnce(() => fresh.p)
    await user.click(screen.getByRole('button', { name: 'Next' }))     // a read that starts while the delete is out
    expect(get).toHaveBeenLastCalledWith('udp-for-benchmarks', 100)
    // the early read lands in the gap between the reply and the re-read it triggers
    onChanged.mockImplementationOnce(() => early.land({ ...first, offset: 100, rows: first.rows.slice(0, 50).map((r) => ({ ...r, index: r.index + 100 })) }))
    go()
    await vi.waitFor(() => expect(get).toHaveBeenCalledTimes(3))
    expect(screen.getByRole('button', { name: 'Add stream' })).toBeDisabled()
    for (const box of screen.queryAllByRole('checkbox')) expect(box).toBeDisabled()
    fresh.land({ ...first, rev: 'r1', stream_count: 149, offset: 100, rows: first.rows.slice(0, 49).map((r) => ({ ...r, index: r.index + 100 })) })
    await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Add stream' })).toBeEnabled())
    expect(screen.getAllByRole('checkbox')[0]).toBeEnabled()
  })

  it('a failed re-read after a shifting write offers Reload and keeps the table locked until a read succeeds', async () => {
    const { get, user, first, go } = await deleteInFlight()
    const broken = held()
    get.mockImplementationOnce(() => broken.p)
    go()
    await vi.waitFor(() => expect(get).toHaveBeenCalledTimes(2))
    broken.fail(new api.ApiError(500, 'storage_error', 'cannot read the profile'))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('cannot read the profile')
    expect(screen.getByRole('button', { name: 'Add stream' })).toBeDisabled()
    expect(screen.getAllByRole('checkbox')[0]).toBeDisabled()
    get.mockResolvedValueOnce({ ...first, rev: 'r1', stream_count: 149, rows: first.rows.slice(1).map((r) => ({ ...r, index: r.index - 1 })) })
    await user.click(screen.getByRole('button', { name: 'Reload' }))
    await vi.waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
    expect(get).toHaveBeenLastCalledWith('udp-for-benchmarks', 0)
    expect(screen.getByRole('button', { name: 'Add stream' })).toBeEnabled()
    expect(screen.getAllByRole('checkbox')[0]).toBeEnabled()
  })

  describe('the selected stream', () => {
    it('shows Properties and Packet tabs once one stream is selected', async () => {
      const { user } = show(py)
      await screen.findByRole('cell', { name: /UDP/ })
      const holder = screen.getByText('Click a stream to edit its properties and packet.')
      expect(holder.closest('.stream-placeholder')).not.toBeNull()
      expect(holder.closest('.stream-placeholder')).toHaveTextContent(/(⌘|Ctrl)-click or Shift-click selects several, for Copy, Duplicate and Delete\./)
      expect(screen.queryByText('Select one stream to see its properties and packet.')).not.toBeInTheDocument()
      await user.click(screen.getByRole('cell', { name: '0' }))
      expect(screen.getByRole('tablist', { name: 'Stream' })).toBeInTheDocument()
      expect(screen.getByLabelText('ISG (µs)')).toBeVisible()
      expect(screen.getByRole('button', { name: 'View packet…', hidden: true })).not.toBeVisible()
      await user.click(screen.getByRole('tab', { name: 'Packet' }))
      expect(screen.getByRole('button', { name: 'View packet…' })).toBeVisible()
    })

    it('Save sends the patch under the stream index with the writer rev', async () => {
      const { get, onChanged, user } = show(py)
      const put = vi.spyOn(api, 'putStreams').mockResolvedValue({ rev: 'r2', stream_count: 1, renamed: {}, cleared: [], warnings: [] })
      await user.click(await screen.findByRole('cell', { name: '0' }))
      await user.clear(screen.getByLabelText('ISG (µs)'))
      await user.type(screen.getByLabelText('ISG (µs)'), '30')
      onChanged.mockClear(); get.mockClear()
      await user.click(screen.getByRole('button', { name: 'Save' }))
      expect(put).toHaveBeenCalledWith('udp-for-benchmarks', { rev: page.rev, changed: { 0: { isg: 30 } } })
      await vi.waitFor(() => expect(get).toHaveBeenCalledTimes(1))
      expect(onChanged).toHaveBeenCalledTimes(1)
    })

    it('a refused Save shows its message and keeps the edit', async () => {
      const { user } = show(py)
      vi.spyOn(api, 'putStreams').mockRejectedValue(new api.ApiError(422, 'invalid_stream', 'stream 0 isg: bad', { index: 0, field: 'isg' }))
      await user.click(await screen.findByRole('cell', { name: '0' }))
      await user.type(screen.getByLabelText('ISG (µs)'), '5')
      await user.click(screen.getByRole('button', { name: 'Save' }))
      expect(await screen.findByRole('alert')).toHaveTextContent('stream 0 isg: bad')
      expect(screen.getByLabelText('ISG (µs)')).toHaveValue('05')
    })

    it('Save and the form are locked while a shifting write is pending, then free after the re-read', async () => {
      const rows = Array.from({ length: 5 }, (_, i) => ({ ...page.rows[0], index: i, stream: { ...page.rows[0].stream, name: `s${i}` } }))
      const first: StreamPage = { ...page, stream_count: 5, names: rows.map((r) => r.stream.name as string), rows }
      const get = vi.spyOn(api, 'getStreams').mockResolvedValueOnce(first)
      let answer: (r: api.DiffReply) => void = () => undefined
      vi.spyOn(api, 'putStreams').mockImplementationOnce(() => new Promise((resolve) => { answer = resolve }))
      render(<ProfileEditor entry={py} onChanged={vi.fn()} />)
      const user = userEvent.setup()
      await user.click(await screen.findByRole('cell', { name: 's1' }))
      expect(screen.getByLabelText('ISG (µs)')).toBeEnabled()
      await user.click(screen.getByRole('button', { name: 'Down' }))
      expect(screen.getByLabelText('ISG (µs)')).toBeDisabled()
      expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
      expect(screen.getByRole('button', { name: 'Previous stream' })).toBeDisabled()
      expect(screen.getByRole('button', { name: 'Next stream' })).toBeDisabled()
      const fresh = held()
      get.mockImplementationOnce(() => fresh.p)
      answer({ rev: 'r1', stream_count: 5, renamed: {}, cleared: [], warnings: [] })
      await vi.waitFor(() => expect(get).toHaveBeenCalledTimes(2))
      expect(screen.getByLabelText('ISG (µs)')).toBeDisabled()          // the read is out, not landed
      const order = [0, 2, 1, 3, 4]
      fresh.land({ ...first, rev: 'r1', rows: order.map((o, i) => ({ ...rows[o], index: i })) })
      await vi.waitFor(() => expect(screen.getByLabelText('ISG (µs)')).toBeEnabled())
      expect(screen.getByRole('button', { name: 'Previous stream' })).toBeEnabled()
    })

    it('Previous and Next are disabled at the ends of a one-stream profile', async () => {
      const { user } = show(py)
      await user.click(await screen.findByRole('cell', { name: '0' }))
      expect(screen.getByRole('button', { name: 'Previous stream' })).toBeDisabled()
      expect(screen.getByRole('button', { name: 'Next stream' })).toBeDisabled()
    })
  })

  describe('import, export and simulate', () => {
    it('Export ▾ links the three formats; a .py entry says its Python is the frozen JSON', async () => {
      const { user } = show(py)
      await user.click(await screen.findByRole('button', { name: 'Export ▾' }))
      const items = screen.getAllByRole('menuitem')
      expect(items.map((i) => [i.textContent, i.getAttribute('href'), i.hasAttribute('download')])).toEqual([
        ['JSON', '/api/studio/profiles/udp-for-benchmarks/export?format=json', true],
        ['YAML', '/api/studio/profiles/udp-for-benchmarks/export?format=yaml', true],
        ['Python (from the frozen JSON, not the .py source)', '/api/studio/profiles/udp-for-benchmarks/export?format=py', true],
      ])
      await user.keyboard('{Escape}')
      expect(screen.queryByRole('menu')).not.toBeInTheDocument()
      cleanup()
      const again = show(withSource({ kind: 'json', path: '/in/x.json' }))
      await again.user.click(await screen.findByRole('button', { name: 'Export ▾' }))
      expect(screen.getByRole('menuitem', { name: 'Python' })).toBeInTheDocument()
    })

    it('Simulate shows the report', async () => {
      const { user } = show(py)
      const run = vi.spyOn(api, 'simulate').mockResolvedValue(sim.udp_for_benchmarks as unknown as api.SimReport)
      await user.click(await screen.findByRole('button', { name: 'Simulate' }))
      expect(run).toHaveBeenCalledWith('udp-for-benchmarks')
      expect(await screen.findByRole('dialog', { name: 'Simulate' })).toHaveTextContent('Round trip: identical')
      await user.click(screen.getByRole('button', { name: 'Close' }))
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })

    // Opens the append dialog and types a pcap path; Import is then one click away.
    async function openAppend(user: ReturnType<typeof userEvent.setup>) {
      await user.click(await screen.findByRole('button', { name: 'Import stream from pcap…' }))
      expect(screen.getByRole('dialog', { name: 'Import streams from pcap' })).toBeInTheDocument()
      await user.type(screen.getByRole('textbox', { name: 'Path' }), '/srv/t.pcap')
    }
    const replyOf = (o: Partial<api.DiffReply> = {}): api.DiffReply => ({ rev: 'r2', stream_count: 3, renamed: {}, cleared: [], warnings: [], ...o })

    it('a pcap append sends the writer rev, locks the table while it is out, then re-reads and shows the paste status', async () => {
      const { get, onChanged, user } = show(py)
      let answer: (r: api.DiffReply) => void = () => undefined
      const imp = vi.spyOn(api, 'importProfile').mockImplementationOnce(() => new Promise((resolve) => { answer = resolve }))
      await openAppend(user)
      expect(screen.getByRole('checkbox', { name: 'Enabled #0' })).toBeEnabled()       // nothing is sent yet
      get.mockClear(); onChanged.mockClear()
      get.mockResolvedValue({ ...page, rev: 'r2', stream_count: 3 })
      await user.click(screen.getByRole('button', { name: 'Import' }))
      expect(imp).toHaveBeenCalledWith(expect.objectContaining({ path: '/srv/t.pcap', kind: 'pcap', append_to: { slug: 'udp-for-benchmarks', rev: page.rev } }))
      expect(screen.getByRole('checkbox', { name: 'Enabled #0' })).toBeDisabled()
      expect(screen.getByRole('button', { name: 'Add stream' })).toBeDisabled()
      answer(replyOf({ renamed: { 1: '1_2' } }))
      await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
      expect(await screen.findByRole('status')).toHaveTextContent('Appended 2 streams from the pcap. Appended as 1_2 (names were taken).')
      await vi.waitFor(() => expect(screen.getByRole('checkbox', { name: 'Enabled #0' })).toBeEnabled())
      expect(get).toHaveBeenCalledTimes(1)
      expect(onChanged).toHaveBeenCalledTimes(1)
      const put = vi.spyOn(api, 'putStreams').mockResolvedValue({ rev: 'r3', stream_count: 3, renamed: {}, cleared: [], warnings: [] })
      await user.click(screen.getByRole('checkbox', { name: 'Enabled #0' }))
      expect(put).toHaveBeenCalledWith('udp-for-benchmarks', { rev: 'r2', changed: { 0: { enabled: false } } })
    })

    it('an append that gets 409 stale ends the writer and shows the editor Reload', async () => {
      const { user } = show(py)
      vi.spyOn(api, 'importProfile').mockRejectedValue(new api.ApiError(409, 'stale', 'The profile changed since it was read; reload it.', { rev: 'other' }))
      await openAppend(user)
      await user.click(screen.getByRole('button', { name: 'Import' }))
      expect(await screen.findByRole('alert')).toHaveTextContent('This profile changed elsewhere.')
      expect(screen.getAllByRole('alert')).toHaveLength(1)
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Reload' })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Add stream' })).toBeDisabled()
    })

    it('an append that gets 404 for its profile marks it gone', async () => {
      const { user } = show(py)
      vi.spyOn(api, 'importProfile').mockRejectedValue(new api.ApiError(404, 'not_found', 'No profile udp-for-benchmarks'))
      await openAppend(user)
      await user.click(screen.getByRole('button', { name: 'Import' }))
      expect(await screen.findByRole('alert')).toHaveTextContent('This profile was renamed or deleted elsewhere.')
    })

    it('an append that gets 422 unlocks the table and shows the message on the dialog error line', async () => {
      const { user } = show(py)
      vi.spyOn(api, 'importProfile').mockRejectedValue(new api.ApiError(422, 'profile_error', 'packet 3 has no IPv4 layer'))
      await openAppend(user)
      await user.click(screen.getByRole('button', { name: 'Import' }))
      const dlg = screen.getByRole('dialog', { name: 'Import streams from pcap' })
      await vi.waitFor(() => expect(screen.getAllByRole('alert')).toHaveLength(1))
      expect(screen.getByRole('alert')).toHaveTextContent('packet 3 has no IPv4 layer')
      expect(dlg).toContainElement(screen.getByRole('alert'))
      expect(screen.getByRole('checkbox', { name: 'Enabled #0' })).toBeEnabled()
      expect(screen.getByRole('button', { name: 'Add stream' })).toBeEnabled()
    })

    it('an append queued behind an in-flight toggle is sent with the toggle reply rev', async () => {
      const { user } = show(py)
      let answer: (r: api.DiffReply) => void = () => undefined
      vi.spyOn(api, 'putStreams').mockImplementationOnce(() => new Promise((resolve) => { answer = resolve }))
      const imp = vi.spyOn(api, 'importProfile').mockResolvedValue(replyOf({ rev: 'r3' }))
      await user.click(await screen.findByRole('checkbox', { name: 'Enabled #0' }))
      await openAppend(user)
      await user.click(screen.getByRole('button', { name: 'Import' }))
      expect(imp).not.toHaveBeenCalled()                 // waits for the toggle
      answer(replyOf({ rev: 'r2', stream_count: 1 }))
      await vi.waitFor(() => expect(imp).toHaveBeenCalledTimes(1))
      expect(imp).toHaveBeenCalledWith(expect.objectContaining({ append_to: { slug: 'udp-for-benchmarks', rev: 'r2' } }))
      expect(await screen.findByRole('status')).toHaveTextContent('Appended 2 streams from the pcap.')
    })

    it('an expired upload does not end the writer', async () => {
      const { user } = show(py)
      vi.spyOn(api, 'uploadFile').mockResolvedValue({ upload_id: 'ab'.repeat(16), name: 't.pcap', size: 1 })
      vi.spyOn(api, 'importProfile').mockRejectedValue(new api.ApiError(404, 'not_found', `No upload ${'ab'.repeat(16)} (it may have expired)`))
      await user.click(await screen.findByRole('button', { name: 'Import stream from pcap…' }))
      await user.click(screen.getByRole('radio', { name: 'From this computer' }))
      await user.upload(screen.getByLabelText('File'), new File(['x'], 't.pcap'))
      await user.click(screen.getByRole('button', { name: 'Import' }))
      expect(await screen.findByRole('alert')).toHaveTextContent('No upload')
      expect(screen.getByRole('dialog', { name: 'Import streams from pcap' })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Add stream' })).toBeEnabled()
    })
  })

  describe('the shift lock is owned per write', () => {
    function deferred<T>() {
      let resolve: (v: T) => void = () => undefined, reject: (e: unknown) => void = () => undefined
      const p = new Promise<T>((res, rej) => { resolve = res; reject = rej })
      return { p, resolve: (v: T) => resolve(v), reject: (e: unknown) => reject(e) }
    }
    const enabledBoxes = () => screen.getAllByRole('checkbox', { name: /^Enabled/ })
    // A three-stream profile; an append whose upload is held, then a delete of s0 started while the upload is out
    // (the append takes its claim only when it is sent, after the upload): the append is queued behind the delete.
    async function deleteThenAppend() {
      const rows = Array.from({ length: 3 }, (_, i) => ({ ...page.rows[0], index: i, stream: { ...page.rows[0].stream, name: `s${i}` } }))
      const first: StreamPage = { ...page, stream_count: 3, names: ['s0', 's1', 's2'], rows }
      const after: StreamPage = { ...first, rev: 'r1', stream_count: 2, names: ['s1', 's2'], rows: rows.slice(1).map((r, i) => ({ ...r, index: i })) }
      const get = vi.spyOn(api, 'getStreams').mockResolvedValueOnce(first)
      const upload = deferred<api.UploadReply>()
      vi.spyOn(api, 'uploadFile').mockImplementationOnce(() => upload.p)
      render(<ProfileEditor entry={py} onChanged={vi.fn()} />)
      const user = userEvent.setup()
      await user.click(await screen.findByRole('button', { name: 'Import stream from pcap…' }))
      await user.click(screen.getByRole('radio', { name: 'From this computer' }))
      await user.upload(screen.getByLabelText('File'), new File(['x'], 't.pcap'))
      await user.click(screen.getByRole('button', { name: 'Import' }))
      const del = deferred<api.DiffReply>()
      const put = vi.spyOn(api, 'putStreams').mockImplementationOnce(() => del.p)
      await user.click(screen.getByRole('cell', { name: 's0' }))
      await user.click(screen.getByRole('button', { name: 'Delete' }))
      await user.click(screen.getByRole('button', { name: 'Confirm delete' }))
      expect(put).toHaveBeenCalledTimes(1)
      upload.resolve({ upload_id: 'ab'.repeat(16), name: 't.pcap', size: 1 })
      return { get, after, del }
    }

    it.each([
      ['422', new api.ApiError(422, 'profile_error', 'packet 3 has no IPv4 layer')],
      ['instant "No upload" 404', new api.ApiError(404, 'not_found', `No upload ${'ab'.repeat(16)} (it may have expired)`)],
    ])('an append refused (%s) after a delete lands keeps the lock until the delete re-read lands', async (_, refusal) => {
      const imp = vi.spyOn(api, 'importProfile').mockRejectedValueOnce(refusal)
      const { get, after, del } = await deleteThenAppend()
      const reread = deferred<StreamPage>()
      get.mockImplementationOnce(() => reread.p)
      expect(imp).not.toHaveBeenCalled()                       // queued behind the delete
      del.resolve({ rev: 'r1', stream_count: 2, renamed: {}, cleared: [], warnings: [] })
      await vi.waitFor(() => expect(imp).toHaveBeenCalledTimes(1))
      expect(await screen.findByRole('alert')).toHaveTextContent(refusal.message)
      expect(get).toHaveBeenCalledTimes(2)                     // the delete's re-read is out
      for (const box of enabledBoxes()) expect(box).toBeDisabled()
      expect(screen.getByRole('button', { name: 'Add stream' })).toBeDisabled()
      reread.resolve(after)
      await vi.waitFor(() => expect(enabledBoxes()[0]).toBeEnabled())
      expect(screen.getByRole('button', { name: 'Add stream' })).toBeEnabled()
    })

    it('a refused delete keeps the lock while the append is out, and until its re-read lands', async () => {
      const appendOut = deferred<api.DiffReply>()
      const imp = vi.spyOn(api, 'importProfile').mockImplementationOnce(() => appendOut.p)
      const { get, after, del } = await deleteThenAppend()
      del.reject(new api.ApiError(422, 'invalid_stream', 'stream 0: bad', { index: 0, field: 'name' }))
      await vi.waitFor(() => expect(imp).toHaveBeenCalledTimes(1))
      for (const box of enabledBoxes()) expect(box).toBeDisabled()
      const reread = deferred<StreamPage>()
      get.mockImplementationOnce(() => reread.p)
      appendOut.resolve({ rev: 'r2', stream_count: 5, renamed: {}, cleared: [], warnings: [] })
      await vi.waitFor(() => expect(get).toHaveBeenCalledTimes(2))
      for (const box of enabledBoxes()) expect(box).toBeDisabled()
      reread.resolve({ ...after, rev: 'r2', stream_count: 5 })
      await vi.waitFor(() => expect(enabledBoxes()[0]).toBeEnabled())
    })

    it('the dialog Import waits while a shifting write is pending', async () => {
      const rows = Array.from({ length: 3 }, (_, i) => ({ ...page.rows[0], index: i, stream: { ...page.rows[0].stream, name: `s${i}` } }))
      const first: StreamPage = { ...page, stream_count: 3, names: ['s0', 's1', 's2'], rows }
      vi.spyOn(api, 'getStreams').mockResolvedValueOnce(first)
      const del = deferred<api.DiffReply>()
      vi.spyOn(api, 'putStreams').mockImplementationOnce(() => del.p)
      render(<ProfileEditor entry={py} onChanged={vi.fn()} />)
      const user = userEvent.setup()
      await user.click(await screen.findByRole('button', { name: 'Import stream from pcap…' }))
      await user.type(screen.getByRole('textbox', { name: 'Path' }), '/srv/t.pcap')
      expect(screen.getByRole('button', { name: 'Import' })).toBeEnabled()
      await user.click(screen.getByRole('cell', { name: 's0' }))
      await user.click(screen.getByRole('button', { name: 'Delete' }))
      await user.click(screen.getByRole('button', { name: 'Confirm delete' }))
      expect(screen.getByRole('button', { name: 'Import' })).toBeDisabled()
      expect(screen.getByText("Waiting for the table's last change to be saved and read back.")).toBeInTheDocument()
    })
  })

  describe('unsaved packet edits', () => {
    const udp = buildFixture.udp as unknown as BuildResult
    const doc: PacketDoc = {
      fileType: 'trex-packet-editor', version: '1.0.0', metadata: { caption: '' }, fePrarameters: { cache_size: '1000' }, feInstructions: [],
      packet: [{ id: 'Ether', fields: [] }, { id: 'IP', fields: [] }, { id: 'UDP', fields: [] }, { id: 'Raw', fields: [] }],
    }
    const rows = Array.from({ length: 2 }, (_, i) => ({ ...page.rows[0], index: i, stream: { ...page.rows[0].stream, name: `s${i}` } }))
    const two: StreamPage = { ...page, stream_count: 2, names: ['s0', 's1'], rows }

    // Streams s0 and s1 on the Packet tab, s0 selected with its packet edited in the embedded editor (`edit` false: opened only).
    async function editing(edit = true) {
      vi.spyOn(api, 'getStreams').mockResolvedValue(two)
      vi.spyOn(api, 'packetDefinitions').mockResolvedValue((defsFixture as unknown as { definitions: Definitions }).definitions)
      vi.spyOn(api, 'buildPacket').mockImplementation(async (d) => ({ ...udp, data: d.packet.map((l, i) => ({ ...(udp.data.find((x) => x.id === l.id) ?? udp.data[3]), id: l.id, offset: udp.data[Math.min(i, 3)].offset })) }))
      vi.spyOn(api, 'payloadClasses').mockResolvedValue({ classes: [] })
      vi.spyOn(api, 'docFromBinary').mockResolvedValue({ doc, warnings: [] })
      const confirm = vi.spyOn(window, 'confirm')
      render(<ProfileEditor entry={py} onChanged={vi.fn()} />)
      const user = userEvent.setup()
      await user.click(await screen.findByRole('cell', { name: 's0' }))
      await user.click(streamTab('Packet'))
      await user.click(screen.getByRole('button', { name: 'Edit packet' }))
      await screen.findByRole('tab', { name: 'Field engine' })
      await vi.waitFor(() => expect(document.querySelector('.layer-list')).not.toBeNull())
      await user.click(within(document.querySelector('.layer-list') as HTMLElement).getByRole('button', { name: 'IP' }))
      if (edit) await editTtl(user)
      return { user, confirm }
    }
    async function editTtl(user: ReturnType<typeof userEvent.setup>) {
      const ttl = await screen.findByRole('textbox', { name: 'TTL' })
      await user.clear(ttl)
      await user.type(ttl, '9{Enter}')
    }
    const streamTab = (name: string) => within(screen.getByRole('tablist', { name: 'Stream' })).getByRole('tab', { name })
    const tableUndo = () => within(screen.getByRole('toolbar', { name: 'Stream actions' })).getByRole('button', { name: 'Undo' })
    const asked = () => screen.queryByRole('dialog', { name: 'Discard the packet edits?' })

    it('switching streams with unsaved packet edits asks first', async () => {
      const { user, confirm } = await editing()
      await user.click(screen.getByRole('cell', { name: 's1' }))
      expect(await screen.findByRole('dialog', { name: 'Discard the packet edits?' })).toBeInTheDocument()
      expect(screen.getByRole('row', { name: /s0/ })).toHaveAttribute('aria-selected', 'true')
      expect(screen.getByRole('row', { name: /s1/ })).toHaveAttribute('aria-selected', 'false')
      await user.click(screen.getByRole('button', { name: 'Discard' }))
      expect(asked()).not.toBeInTheDocument()
      expect(screen.getByRole('row', { name: /s1/ })).toHaveAttribute('aria-selected', 'true')
      expect(screen.getByRole('button', { name: 'Edit packet' })).toBeInTheDocument()      // the edits went with the old stream
      expect(confirm).not.toHaveBeenCalled()
    })

    it('Keep editing keeps the edits', async () => {
      const { user } = await editing()
      await user.click(screen.getByRole('cell', { name: 's1' }))
      await user.click(await screen.findByRole('button', { name: 'Keep editing' }))
      expect(asked()).not.toBeInTheDocument()
      expect(screen.getByRole('row', { name: /s0/ })).toHaveAttribute('aria-selected', 'true')
      expect(screen.getByRole('button', { name: 'Reset TTL to default' })).toBeInTheDocument()      // the edit is still there
      expect(screen.getByRole('tab', { name: 'Field engine' })).toBeInTheDocument()
    })

    it('Escape closes the question and nothing else', async () => {
      const { user } = await editing()
      await user.click(screen.getByRole('cell', { name: 's1' }))
      await screen.findByRole('dialog', { name: 'Discard the packet edits?' })
      await user.keyboard('{Escape}')
      expect(asked()).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Reset TTL to default' })).toBeInTheDocument()      // the edit is still there
    })

    it('switching the detail tab asks too, and Discard starts the editor again', async () => {
      const { user } = await editing()
      await user.click(streamTab('Properties'))
      expect(await screen.findByRole('dialog', { name: 'Discard the packet edits?' })).toBeInTheDocument()
      expect(streamTab('Packet')).toHaveAttribute('aria-selected', 'true')
      await user.click(screen.getByRole('button', { name: 'Discard' }))
      expect(streamTab('Properties')).toHaveAttribute('aria-selected', 'true')
      await user.click(streamTab('Packet'))
      expect(screen.getByRole('button', { name: 'Edit packet' })).toBeInTheDocument()
    })

    it('without edits nothing is asked', async () => {
      vi.spyOn(api, 'getStreams').mockResolvedValue(two)
      render(<ProfileEditor entry={py} onChanged={vi.fn()} />)
      const user = userEvent.setup()
      await user.click(await screen.findByRole('cell', { name: 's0' }))
      await user.click(screen.getByRole('cell', { name: 's1' }))
      await user.click(streamTab('Packet'))
      expect(asked()).not.toBeInTheDocument()
      expect(screen.getByRole('row', { name: /s1/ })).toHaveAttribute('aria-selected', 'true')
    })

    it('with unsaved edits the actions that move the selection are off, saying why', async () => {
      const { user } = await editing()
      const bar = within(screen.getByRole('toolbar', { name: 'Stream actions' }))
      await user.click(screen.getByRole('cell', { name: 's1' }))     // asks; Keep editing below
      await user.click(await screen.findByRole('button', { name: 'Keep editing' }))
      for (const name of ['Up', 'Down', 'Duplicate', 'Paste', 'Delete']) {
        expect(bar.getByRole('button', { name })).toBeDisabled()
        expect(bar.getByRole('button', { name })).toHaveAttribute('title', expect.stringContaining('unsaved edits'))
      }
      for (const name of ['Add stream', 'Import stream from pcap…']) expect(screen.getByRole('button', { name })).toBeDisabled()
      expect(bar.getByRole('button', { name: 'Copy' })).toBeEnabled()
    })

    it('Undo is off while the edits are unsaved', async () => {
      const { user } = await editing(false)
      vi.spyOn(api, 'putStreams').mockResolvedValue({ rev: 'r1', stream_count: 2, renamed: {}, cleared: [], warnings: [] })
      await user.click(screen.getByRole('checkbox', { name: 'Enabled s1' }))
      await vi.waitFor(() => expect(tableUndo()).toBeEnabled())          // a clean editor: Undo works
      await editTtl(user)
      expect(tableUndo()).toBeDisabled()
    })

    it('Ctrl+Z in the table does nothing while the edits are unsaved', async () => {
      const { user } = await editing(false)
      const put = vi.spyOn(api, 'putStreams').mockResolvedValue({ rev: 'r1', stream_count: 2, renamed: {}, cleared: [], warnings: [] })
      await user.click(screen.getByRole('checkbox', { name: 'Enabled s1' }))
      await vi.waitFor(() => expect(tableUndo()).toBeEnabled())
      await editTtl(user)
      screen.getByRole('table', { name: 'Streams' }).focus()
      await user.keyboard('{Control>}z{/Control}')
      await new Promise((r) => setTimeout(r, 20))
      expect(put).toHaveBeenCalledTimes(1)           // only the toggle
    })

    it('an open delete confirm cannot be confirmed once the edits are unsaved', async () => {
      const { user } = await editing(false)
      await user.click(screen.getByRole('button', { name: 'Delete' }))   // s0 is selected: the confirm opens
      await editTtl(user)
      expect(screen.getByRole('button', { name: 'Confirm delete' })).toBeDisabled()
    })

    it('a write that lands with a new selection asks before it unmounts the edits, and Keep editing keeps them', async () => {
      const { user } = await editing(false)
      let answer: (r: api.DiffReply) => void = () => undefined
      vi.spyOn(api, 'getTemplate').mockResolvedValue(page.rows[0].stream)
      vi.spyOn(api, 'putStreams').mockImplementationOnce(() => new Promise((resolve) => { answer = resolve }))
      await user.click(screen.getByRole('button', { name: 'Add stream' }))      // out, and the editor is still clean
      await vi.waitFor(() => expect(api.putStreams).toHaveBeenCalled())
      await editTtl(user)                                                         // edited while it is out
      answer({ rev: 'r1', stream_count: 3, renamed: {}, cleared: [], warnings: [] })
      expect(await screen.findByRole('dialog', { name: 'Discard the packet edits?' })).toBeInTheDocument()
      await user.click(screen.getByRole('button', { name: 'Keep editing' }))
      expect(screen.getByRole('row', { name: /s0/ })).toHaveAttribute('aria-selected', 'true')
      expect(screen.getByRole('button', { name: 'Reset TTL to default' })).toBeInTheDocument()
    })

    it('a Reload after a stale writer asks before it drops the edits', async () => {
      const { user } = await editing()
      vi.spyOn(api, 'putStreams').mockRejectedValue(new api.ApiError(409, 'stale', 'The profile changed', { rev: 'newer' }))
      await user.click(screen.getByRole('checkbox', { name: 'Enabled s1' }))
      await user.click(await screen.findByRole('button', { name: 'Reload' }))
      await user.click(await screen.findByRole('button', { name: 'Keep editing' }))
      expect(screen.getByRole('button', { name: 'Reset TTL to default' })).toBeInTheDocument()
    })

    it('a Save puts its undo entry in the table\'s history', async () => {
      const { user } = await editing()
      vi.spyOn(api, 'toStream').mockResolvedValue({ packet: { binary: 'TkVX', meta: '' }, vm: { instructions: [] } })
      vi.spyOn(api, 'streamSidecar').mockResolvedValue({ rev: page.rev, entry: {} })
      vi.spyOn(api, 'putStreams').mockResolvedValue({ rev: 'r1', stream_count: 2, renamed: {}, cleared: [], warnings: [] })
      expect(tableUndo()).toBeDisabled()
      await user.click(screen.getByRole('button', { name: 'Save' }))
      await vi.waitFor(() => expect(tableUndo()).toBeEnabled())
      expect(tableUndo()).toHaveAttribute('title', 'Undo editing stream 0 (Ctrl+Z)')
    })
  })
})
