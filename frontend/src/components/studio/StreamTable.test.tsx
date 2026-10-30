import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import type { DiffReply, LibraryEntry, StreamJson, StreamPage } from '../../api'
import { ProfileEditor } from './ProfileEditor'
import { copyStreams } from './streamClipboard'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

const entry: LibraryEntry = {
  slug: 'p', name: 'p', path: '/lib/p.json', stream_count: 3, created: 'c', updated: '2026-01-01T00:00:00.000Z', source_missing: false, load_error: null,
  source: { kind: 'json', path: '/in/p.json', args: [], tunables: null, ports_differ: false, imported: 'i' },
}
const stream = (name: string, next?: string): StreamJson => ({
  name, ...(next ? { next } : {}), enabled: true, self_start: true, isg: 0, action_count: 0, flags: 0,
  mode: next ? { type: 'single_burst', rate: { type: 'pps', value: 10 }, total_pkts: 10 } : { type: 'continuous', rate: { type: 'pps', value: 1 } },
  packet: { binary: 'AAAA', meta: '' }, vm: { instructions: [] }, flow_stats: { enabled: false },
})
const pageOf = (streams: StreamJson[], o: Partial<StreamPage> = {}): StreamPage => ({
  rev: 'r0', stream_count: streams.length, names: streams.map((s) => s.name ?? null), offset: 0, warnings: [],
  rows: streams.map((s, index) => ({ index, stream: s, length: 60, summary: { proto: 'UDP', info: `Ether / IP / UDP ${String(s.name)}` }, has_editor: false })), ...o,
})
const reply = (o: Partial<DiffReply> = {}): DiffReply => ({ rev: 'r1', stream_count: 3, renamed: {}, cleared: [], warnings: [], ...o })

async function open(first: StreamPage, ...later: StreamPage[]) {
  const get = vi.spyOn(api, 'getStreams').mockResolvedValue(later.at(-1) ?? first)
  get.mockResolvedValueOnce(first)
  for (const p of later) get.mockResolvedValueOnce(p)
  const put = vi.spyOn(api, 'putStreams').mockResolvedValue(reply())
  const onChanged = vi.fn()
  render(<ProfileEditor entry={entry} onChanged={onChanged} />)
  await screen.findByRole('table', { name: 'Streams' })
  return { get, put, onChanged, user: userEvent.setup() }
}
// The Name cell (column 2): a stream's name also appears in another's Next cell.
const nameCell = (name: string) => screen.getAllByRole('cell', { name }).find((c) => (c as HTMLTableCellElement).cellIndex === 2) as HTMLElement
const rowOf = (name: string) => nameCell(name).closest('tr') as HTMLElement
const table = () => screen.getByRole('table', { name: 'Streams' })

describe('StreamTable', () => {
  it('warns, once per pair, of an enabled stream whose Next is disabled, and shows nothing otherwise', async () => {
    await open(pageOf([stream('S0', 'S1'), stream('S1')], { warnings: [{ index: 0, name: 'S0', next: 'S1' }, { index: 1, name: null, next: 'S0' }] }))
    const lines = screen.getAllByRole('status').map((e) => e.textContent)
    expect(lines).toContain("S0 goes to S1, which is disabled: TRex refuses this profile at assign until S0's Next changes.")
    expect(lines).toContain("#1 goes to S0, which is disabled: TRex refuses this profile at assign until #1's Next changes.")
    cleanup()
    await open(pageOf([stream('S0'), stream('S1')]))
    expect(screen.queryByText(/which is disabled/)).not.toBeInTheDocument()
  })

  it('enabled saves at once with a one-field patch', async () => {
    const { put, get, onChanged, user } = await open(pageOf([stream('a'), stream('b')]))
    await user.click(screen.getByRole('checkbox', { name: 'Enabled b' }))
    expect(put).toHaveBeenCalledWith('p', { rev: 'r0', changed: { 1: { enabled: false } } })
    await vi.waitFor(() => expect(get).toHaveBeenCalledTimes(2))
    expect(onChanged).toHaveBeenCalled()
  })

  it('inline rename sends the new name and Escape cancels', async () => {
    const { put, user } = await open(pageOf([stream('a'), stream('b')]))
    await user.dblClick(nameCell('a'))
    await user.type(screen.getByRole('textbox', { name: 'Name of stream 0' }), 'x{Escape}')
    expect(screen.queryByRole('textbox', { name: /^Name of stream/ })).not.toBeInTheDocument()
    expect(nameCell('a')).toBeInTheDocument()
    expect(put).not.toHaveBeenCalled()
    await user.click(nameCell('b'))
    await user.keyboard('{F2}')
    const box = screen.getByRole('textbox', { name: 'Name of stream 1' })
    expect(box).toHaveFocus()
    await user.clear(box)
    await user.type(box, 'renamed{Enter}')
    expect(put).toHaveBeenCalledWith('p', { rev: 'r0', changed: { 1: { name: 'renamed' } } })
  })

  it('inline rename refuses a name with a quote or one that is taken, sending nothing', async () => {
    const { put, user } = await open(pageOf([stream('a'), stream('b')]))
    await user.dblClick(nameCell('a'))
    await user.type(screen.getByRole('textbox', { name: 'Name of stream 0' }), "'{Enter}")
    expect(await screen.findByRole('alert')).toHaveTextContent('Name of stream 0: Use 1 to 80 printable ASCII characters')
    await user.dblClick(nameCell('b'))
    const box = screen.getByRole('textbox', { name: 'Name of stream 1' })
    await user.clear(box)
    await user.type(box, 'a{Enter}')
    expect(await screen.findByRole('alert')).toHaveTextContent('taken by another stream')
    expect(put).not.toHaveBeenCalled()
  })

  it('marks the open row only when exactly one is selected', async () => {
    const { user } = await open(pageOf([stream('a'), stream('b'), stream('c')]))
    await user.click(nameCell('a'))
    expect(rowOf('a')).toHaveAttribute('data-open', 'true')
    expect(rowOf('b')).not.toHaveAttribute('data-open')
    await user.keyboard('{Control>}')
    await user.click(nameCell('b'))
    await user.keyboard('{/Control}')
    expect(rowOf('a')).toHaveAttribute('aria-selected', 'true')
    expect(rowOf('b')).toHaveAttribute('aria-selected', 'true')
    expect(rowOf('a')).not.toHaveAttribute('data-open')
    expect(rowOf('b')).not.toHaveAttribute('data-open')
    expect(screen.getByText('2 streams selected. Click one to edit it.')).toBeInTheDocument()
    expect(screen.queryByText(/Select one stream/)).not.toBeInTheDocument()
  })

  it('frames the table and ends each row with a hidden chevron', async () => {
    await open(pageOf([stream('a')]))
    expect(table().closest('.stream-table-frame')).not.toBeNull()
    const chev = rowOf('a').querySelector('td.chev')
    expect(chev).toHaveAttribute('aria-hidden', 'true')
    expect(chev).toHaveTextContent('›')
  })

  it('Alt+ArrowDown moves the selection down and keeps it selected', async () => {
    const { put, user } = await open(pageOf([stream('a'), stream('b'), stream('c')]), pageOf([stream('b'), stream('a'), stream('c')], { rev: 'r1' }))
    await user.click(nameCell('a'))
    expect(rowOf('a')).toHaveAttribute('aria-selected', 'true')
    await user.keyboard('{Alt>}{ArrowDown}{/Alt}')
    expect(put).toHaveBeenCalledWith('p', { rev: 'r0', order: [1, 0, 2] })
    await vi.waitFor(() => expect(screen.getAllByRole('row')[1]).toHaveTextContent('b'))
    expect(rowOf('a')).toHaveAttribute('aria-selected', 'true')
    expect(rowOf('b')).toHaveAttribute('aria-selected', 'false')
    expect(table()).toHaveFocus()
  })

  it('Ctrl+C then Ctrl+V pastes after the selection and reports renames', async () => {
    const { put, user } = await open(pageOf([stream('a'), stream('b'), stream('c')]))
    put.mockResolvedValue(reply({ stream_count: 4, renamed: { a: 'a_2' } }))
    await user.click(nameCell('a'))
    await user.keyboard('{Control>}c{/Control}')
    await user.keyboard('{Control>}v{/Control}')
    expect(put).toHaveBeenCalledWith('p', { rev: 'r0', added: [stream('a')], order: [0, '+0', 1, 2] })
    expect(await screen.findByRole('status')).toHaveTextContent('Pasted as a_2 (names were taken).')
  })

  it('a paste whose next names nothing here reports the streams that now stop', async () => {
    const { put, user } = await open(pageOf([stream('a', 'b'), stream('b')]))
    put.mockResolvedValue(reply({ stream_count: 3, renamed: { a: 'a_2' }, cleared: ['a_2'] }))
    await user.click(nameCell('a'))
    await user.click(screen.getByRole('button', { name: 'Copy' }))
    await user.click(screen.getByRole('button', { name: 'Paste' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Pasted as a_2 (names were taken). a_2 now stop: the stream they went to is not in this profile.')
  })

  it('deleting a chained stream reports the streams that now stop', async () => {
    const { put, user } = await open(pageOf([stream('a', 'b'), stream('b')]))
    put.mockResolvedValue(reply({ stream_count: 1, cleared: ['a'] }))
    await user.click(nameCell('b'))
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    expect(put).not.toHaveBeenCalled()
    await user.click(within(screen.getByRole('group', { name: 'Delete streams' })).getByRole('button', { name: 'Confirm delete' }))
    expect(put).toHaveBeenCalledWith('p', { rev: 'r0', removed: [1] })
    expect(await screen.findByRole('status')).toHaveTextContent('a now stop instead of going to the deleted stream.')
  })

  it('Del with a multi-selection deletes all of them after confirm', async () => {
    const { put, user } = await open(pageOf([stream('a'), stream('b'), stream('c'), stream('d')]))
    await user.click(nameCell('b'))
    await user.keyboard('{Shift>}')
    await user.click(nameCell('d'))                                 // b to d
    await user.keyboard('{/Shift}{Control>}')
    await user.click(nameCell('c'))                                 // c out
    await user.click(nameCell('a'))                                 // a in
    await user.keyboard('{/Control}')
    expect(['a', 'b', 'c', 'd'].map((n) => rowOf(n).getAttribute('aria-selected'))).toEqual(['true', 'true', 'false', 'true'])
    await user.keyboard('{Delete}')
    const confirm = screen.getByRole('group', { name: 'Delete streams' })
    expect(confirm).toHaveTextContent('Delete 3 streams?')
    expect(put).not.toHaveBeenCalled()
    await user.click(within(confirm).getByRole('button', { name: 'Confirm delete' }))
    expect(put).toHaveBeenCalledWith('p', { rev: 'r0', removed: [0, 1, 3] })
  })

  it('a delete locks index-based edits until its re-read lands, then a toggle hits the right row', async () => {
    const { get, put, user } = await open(pageOf([stream('a'), stream('b'), stream('c'), stream('d')]))
    copyStreams([stream('x')])                                          // Ctrl+V would paste without the lock
    let answer: (r: DiffReply) => void = () => undefined
    put.mockImplementationOnce(() => new Promise<DiffReply>((resolve) => { answer = resolve }))
    let land: (p: StreamPage) => void = () => undefined
    get.mockImplementationOnce(() => new Promise<StreamPage>((resolve) => { land = resolve }))
    await user.click(nameCell('b'))
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await user.click(screen.getByRole('button', { name: 'Confirm delete' }))
    expect(put).toHaveBeenCalledTimes(1)
    // the delete is in flight and b is still selected, so each key below would act without the lock
    expect(rowOf('b')).toHaveAttribute('aria-selected', 'true')
    const tryEverything = async () => {
      table().focus()
      await user.keyboard('{Delete}{Alt>}{ArrowUp}{/Alt}{Alt>}{ArrowDown}{/Alt}{Control>}v{/Control}{F2}')
      await user.dblClick(nameCell('c'))
      await user.click(screen.getByRole('checkbox', { name: 'Enabled d' }))
      await user.click(nameCell('c'))
      for (const b of ['Up', 'Down', 'Duplicate', 'Copy', 'Paste', 'Delete', 'Add stream']) expect(screen.getByRole('button', { name: b })).toBeDisabled()
      expect(screen.getByRole('checkbox', { name: 'Enabled d' })).toBeDisabled()
      expect(screen.queryByRole('textbox', { name: /^Name of stream/ })).not.toBeInTheDocument()
      expect(screen.queryByRole('group', { name: 'Delete streams' })).not.toBeInTheDocument()
      expect(rowOf('c')).toHaveAttribute('aria-selected', 'false')
      expect(put).toHaveBeenCalledTimes(1)
    }
    await tryEverything()
    answer(reply({ rev: 'r1', stream_count: 3 }))
    await vi.waitFor(() => expect(get).toHaveBeenCalledTimes(2))            // the re-read is out, not landed
    await tryEverything()
    land({ ...pageOf([stream('a'), stream('c'), stream('d')]), rev: 'r1' })
    await vi.waitFor(() => expect(screen.getByRole('checkbox', { name: 'Enabled d' })).toBeEnabled())
    await user.click(screen.getByRole('checkbox', { name: 'Enabled d' }))
    expect(put).toHaveBeenLastCalledWith('p', { rev: 'r1', changed: { 2: { enabled: false } } })
  })

  it('an open delete confirm blocks every index-shifting action, so it never deletes a moved stream', async () => {
    const tpl = stream('template')
    const template = vi.spyOn(api, 'getTemplate').mockResolvedValue(tpl)
    const { put, user } = await open(pageOf([stream('a'), stream('b'), stream('c')]))
    copyStreams([stream('x')])
    await user.click(nameCell('b'))
    table().focus()
    await user.keyboard('{Delete}')
    expect(screen.getByRole('group', { name: 'Delete streams' })).toHaveTextContent('Delete 1 stream?')
    table().focus()
    await user.keyboard('{Alt>}{ArrowDown}{/Alt}{Alt>}{ArrowUp}{/Alt}{Control>}v{/Control}')
    for (const b of ['Up', 'Down', 'Duplicate', 'Paste', 'Delete', 'Add stream']) {
      expect(screen.getByRole('button', { name: b })).toBeDisabled()
      await user.click(screen.getByRole('button', { name: b }))
    }
    expect(put).not.toHaveBeenCalled()
    expect(template).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Confirm delete' }))
    expect(put).toHaveBeenCalledTimes(1)
    expect(put).toHaveBeenCalledWith('p', { rev: 'r0', removed: [1] })          // still b: nothing moved
    await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Add stream' })).toBeEnabled())
  })

  it('a refused toggle puts the checkbox back', async () => {
    const { put, user } = await open(pageOf([stream('a'), stream('b')]))
    put.mockRejectedValue(new api.ApiError(422, 'invalid_stream', 'stream 1 enabled: no', { index: 1, field: 'enabled' }))
    await user.click(screen.getByRole('checkbox', { name: 'Enabled b' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('stream 1 enabled: no')
    expect(screen.getByRole('checkbox', { name: 'Enabled b' })).toBeChecked()
  })

  it('a refused move puts the selection back', async () => {
    const { put, user } = await open(pageOf([stream('a'), stream('b'), stream('c')]))
    put.mockRejectedValue(new api.ApiError(422, 'invalid_stream', 'order: must list every surviving stream', { index: null, field: 'order' }))
    await user.click(nameCell('a'))
    await user.keyboard('{Alt>}{ArrowDown}{/Alt}')
    expect(await screen.findByRole('alert')).toHaveTextContent('order: must list')
    expect(rowOf('a')).toHaveAttribute('aria-selected', 'true')
    expect(rowOf('b')).toHaveAttribute('aria-selected', 'false')
    expect(screen.getByRole('button', { name: 'Up' })).toBeDisabled()             // the selection is stream 0 again
  })

  it('keys do nothing while the table is not focused', async () => {
    const { put, user } = await open(pageOf([stream('a'), stream('b')]))
    await user.click(nameCell('a'))
    await user.keyboard('{Control>}c{/Control}')
    screen.getByRole('button', { name: 'Add stream' }).focus()
    await user.keyboard('{Delete}{Alt>}{ArrowDown}{/Alt}{Control>}v{/Control}{F2}')
    expect(put).not.toHaveBeenCalled()
    expect(screen.queryByRole('group', { name: 'Delete streams' })).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: /^Name of stream/ })).not.toBeInTheDocument()
  })

  it('Add stream appends the template', async () => {
    const tpl = stream('template')
    vi.spyOn(api, 'getTemplate').mockResolvedValue(tpl)
    const { put, user } = await open(pageOf([stream('a')]), pageOf([stream('a'), tpl], { rev: 'r1' }))
    put.mockResolvedValue(reply({ stream_count: 2 }))
    await user.click(screen.getByRole('button', { name: 'Add stream' }))
    expect(put).toHaveBeenCalledWith('p', { rev: 'r0', added: [tpl] })
    await vi.waitFor(() => expect(rowOf('template')).toHaveAttribute('aria-selected', 'true'))
  })

  it('pages of 100 with a range line', async () => {
    const many = (from: number) => Array.from({ length: 100 }, (_, i) => stream(`s${from + i}`))
    const first = pageOf(many(0), { stream_count: 22000 })
    const second = { ...pageOf(many(100), { stream_count: 22000, offset: 100 }) }
    second.rows = second.rows.map((r) => ({ ...r, index: r.index + 100 }))
    const { get, user } = await open(first, second)
    expect(get).toHaveBeenCalledWith('p', 0)
    expect(screen.getByText('rows 1–100 of 22000')).toBeInTheDocument()
    expect(screen.getAllByRole('row')).toHaveLength(101)
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(get).toHaveBeenLastCalledWith('p', 100)
    expect(await screen.findByText('rows 101–200 of 22000')).toBeInTheDocument()
    expect(nameCell('s100')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Previous' })).toBeEnabled()
  })

  it('shows the A5.6 columns, with the length including FCS and a missing packet as a dash', async () => {
    const p = pageOf([stream('a', 'b'), stream('b')])
    p.rows[1] = { ...p.rows[1], length: null, summary: { proto: '?', info: 'no usable packet (Error: bad)' } }
    await open(p)
    const heads = screen.getAllByRole('columnheader').map((h) => h.textContent)
    expect(heads).toEqual(['Enabled', '#', 'Name', 'Packet', 'Length (with FCS)', 'Mode', 'Rate', 'Next'])
    expect(rowOf('a')).toHaveTextContent('0aUDP Ether / IP / UDP a64single burst, 10 pkts10 ppsb')
    expect(within(rowOf('b')).getAllByRole('cell')[4]).toHaveTextContent('—')
    expect(rowOf('b')).toHaveTextContent('no usable packet (Error: bad)')
  })

  describe('undo', () => {
    const undoBtn = () => screen.getByRole('button', { name: 'Undo' })
    const redoBtn = () => screen.getByRole('button', { name: 'Redo' })
    const withEditor = (p: StreamPage, ...indices: number[]) => { for (const i of indices) p.rows[i].has_editor = true; return p }
    const doc = { editor: { doc: { metadata: { caption: 'b' } }, binary: 'AAAA', vm: { instructions: [] } } }

    it('Ctrl+Z undoes the last table action through the writer', async () => {
      const { put, user } = await open(pageOf([stream('a'), stream('b')]), pageOf([stream('a'), stream('b')], { rev: 'r1' }))
      put.mockResolvedValue(reply({ rev: 'r1', stream_count: 2 }))
      expect(undoBtn()).toBeDisabled()
      await user.click(screen.getByRole('checkbox', { name: 'Enabled b' }))
      await vi.waitFor(() => expect(undoBtn()).toBeEnabled())
      table().focus()
      put.mockResolvedValue(reply({ rev: 'r2', stream_count: 2 }))
      await user.keyboard('{Control>}z{/Control}')
      expect(put).toHaveBeenLastCalledWith('p', { rev: 'r1', changed: { 1: { enabled: true } } })
      await vi.waitFor(() => expect(redoBtn()).toBeEnabled())
      expect(await screen.findByRole('status')).toHaveTextContent('Undid disabling stream 1.')
      table().focus()
      await user.keyboard('{Control>}y{/Control}')
      expect(put).toHaveBeenLastCalledWith('p', { rev: 'r2', changed: { 1: { enabled: false } } })
      await vi.waitFor(() => expect(undoBtn()).toBeEnabled())
      table().focus()
      await user.keyboard('{Control>}z{/Control}')
      await vi.waitFor(() => expect(redoBtn()).toBeEnabled())
      table().focus()
      await user.keyboard('{Control>}{Shift>}z{/Shift}{/Control}')
      expect(put).toHaveBeenCalledTimes(5)
    })

    it('a refused write pushes no undo entry', async () => {
      const { put, user } = await open(pageOf([stream('a'), stream('b')]))
      put.mockRejectedValue(new api.ApiError(422, 'invalid_stream', 'stream 1 enabled: no', { index: 1, field: 'enabled' }))
      await user.click(screen.getByRole('checkbox', { name: 'Enabled b' }))
      expect(await screen.findByRole('alert')).toHaveTextContent('stream 1 enabled: no')
      expect(undoBtn()).toBeDisabled()
      table().focus()
      await user.keyboard('{Control>}z{/Control}')
      expect(put).toHaveBeenCalledTimes(1)
    })

    it('undo is disabled while a write is pending', async () => {
      const { put, user } = await open(pageOf([stream('a'), stream('b')]), pageOf([stream('a'), stream('b')], { rev: 'r1' }))
      put.mockResolvedValue(reply({ rev: 'r1', stream_count: 2 }))
      await user.click(screen.getByRole('checkbox', { name: 'Enabled a' }))
      await vi.waitFor(() => expect(undoBtn()).toBeEnabled())
      let answer: (r: DiffReply) => void = () => undefined
      put.mockImplementationOnce(() => new Promise<DiffReply>((resolve) => { answer = resolve }))
      await user.click(screen.getByRole('checkbox', { name: 'Enabled b' }))
      expect(put).toHaveBeenCalledTimes(2)
      expect(undoBtn()).toBeDisabled()
      table().focus()
      await user.keyboard('{Control>}z{/Control}')
      expect(put).toHaveBeenCalledTimes(2)
      answer(reply({ rev: 'r2', stream_count: 2 }))
      await vi.waitFor(() => expect(undoBtn()).toBeEnabled())
      put.mockResolvedValue(reply({ rev: 'r3', stream_count: 2 }))
      await user.click(undoBtn())
      expect(put).toHaveBeenLastCalledWith('p', { rev: 'r2', changed: { 1: { enabled: true } } })
    })

    it('undo of a delete goes through onShift', async () => {
      vi.spyOn(api, 'streamSidecar').mockResolvedValue({ rev: 'r0', entry: doc })
      const base = [stream('a'), stream('b'), stream('c')]
      const { get, put, user } = await open(withEditor(pageOf(base), 1), pageOf([stream('a'), stream('c')], { rev: 'r1' }))
      put.mockResolvedValue(reply({ rev: 'r1', stream_count: 2 }))
      await user.click(nameCell('b'))
      await user.click(screen.getByRole('button', { name: 'Delete' }))
      await user.click(screen.getByRole('button', { name: 'Confirm delete' }))
      expect(put).toHaveBeenCalledWith('p', { rev: 'r0', removed: [1] })
      await vi.waitFor(() => expect(undoBtn()).toBeEnabled())
      let answer: (r: DiffReply) => void = () => undefined
      put.mockImplementationOnce(() => new Promise<DiffReply>((resolve) => { answer = resolve }))
      let land: (p: StreamPage) => void = () => undefined
      get.mockImplementationOnce(() => new Promise<StreamPage>((resolve) => { land = resolve }))
      await user.click(undoBtn())
      expect(put).toHaveBeenLastCalledWith('p', { rev: 'r1', added: [stream('b')], order: [0, '+0', 1], added_sidecar: [doc] })
      // the undo shifts indices, so index input is blocked like the original delete, until the page read after it lands
      for (const b of ['Undo', 'Redo', 'Up', 'Down', 'Duplicate', 'Paste', 'Delete', 'Add stream']) expect(screen.getByRole('button', { name: b })).toBeDisabled()
      expect(screen.getByRole('checkbox', { name: 'Enabled c' })).toBeDisabled()
      answer(reply({ rev: 'r2', stream_count: 3 }))
      await vi.waitFor(() => expect(get).toHaveBeenCalledTimes(3))
      expect(screen.getByRole('checkbox', { name: 'Enabled c' })).toBeDisabled()
      land({ ...pageOf(base), rev: 'r2' })
      await vi.waitFor(() => expect(screen.getByRole('checkbox', { name: 'Enabled c' })).toBeEnabled())
      await vi.waitFor(() => expect(redoBtn()).toBeEnabled())
    })

    it('a stale writer clears the history', async () => {
      const { put, user } = await open(pageOf([stream('a'), stream('b')]), pageOf([stream('a'), stream('b')], { rev: 'r1' }))
      put.mockResolvedValue(reply({ rev: 'r1', stream_count: 2 }))
      await user.click(screen.getByRole('checkbox', { name: 'Enabled a' }))
      await vi.waitFor(() => expect(undoBtn()).toBeEnabled())
      expect(undoBtn()).toHaveAttribute('title', 'Undo disabling stream 0 (Ctrl+Z)')
      put.mockRejectedValue(new api.ApiError(409, 'stale', 'The profile changed since it was read; reload it.', { rev: 'other' }))
      await user.click(screen.getByRole('checkbox', { name: 'Enabled b' }))
      // the history is empty as soon as the writer is stale, before Reload makes a new one
      await screen.findByRole('button', { name: 'Reload' })
      expect(undoBtn()).toBeDisabled()
      expect(undoBtn()).not.toHaveAttribute('title')
      await user.click(screen.getByRole('button', { name: 'Reload' }))
      await screen.findByRole('table', { name: 'Streams' })
      expect(undoBtn()).toBeDisabled()
      expect(redoBtn()).toBeDisabled()
    })

    it('undo reports renames when a restored name was taken', async () => {
      const { put, user } = await open(pageOf([stream('a'), stream('b')]), pageOf([stream('a')], { rev: 'r1' }))
      put.mockResolvedValue(reply({ rev: 'r1', stream_count: 1 }))
      await user.click(nameCell('b'))
      await user.click(screen.getByRole('button', { name: 'Delete' }))
      await user.click(screen.getByRole('button', { name: 'Confirm delete' }))
      await vi.waitFor(() => expect(undoBtn()).toBeEnabled())
      put.mockResolvedValue(reply({ rev: 'r2', stream_count: 2, renamed: { b: 'b_2' } }))
      await user.click(undoBtn())
      expect(await screen.findByRole('status')).toHaveTextContent('Undid deleting a stream. Restored as b_2 (names were taken).')
    })

    it('a rename to nothing is undone with the next it cleared', async () => {
      const { put, user } = await open(pageOf([stream('a', 'b'), stream('b')]), pageOf([stream('a'), stream('')], { rev: 'r1' }))
      put.mockResolvedValue(reply({ rev: 'r1', stream_count: 2, cleared: ['a'] }))
      await user.dblClick(nameCell('b'))
      const box = screen.getByRole('textbox', { name: 'Name of stream 1' })
      await user.clear(box)
      await user.keyboard('{Enter}')
      await vi.waitFor(() => expect(undoBtn()).toBeEnabled())
      put.mockResolvedValue(reply({ rev: 'r2', stream_count: 2 }))
      await user.click(undoBtn())
      expect(put).toHaveBeenLastCalledWith('p', { rev: 'r1', changed: { 1: { name: 'b' }, 0: { next: 'b' } } })
    })

    it('duplicate and paste carry the sidecar entries', async () => {
      const fetch = vi.spyOn(api, 'streamSidecar').mockResolvedValue({ rev: 'r0', entry: doc })
      const { put, user } = await open(withEditor(pageOf([stream('a'), stream('b')]), 0))
      put.mockResolvedValue(reply({ stream_count: 3 }))
      await user.click(nameCell('a'))
      await user.click(screen.getByRole('button', { name: 'Duplicate' }))
      expect(fetch).toHaveBeenCalledWith('p', 0)
      await vi.waitFor(() => expect(put).toHaveBeenCalledWith('p', { rev: 'r0', added: [stream('a')], added_sidecar: [doc], order: [0, '+0', 1] }))
      await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Duplicate' })).toBeEnabled())
      await user.click(nameCell('a'))
      await user.click(screen.getByRole('button', { name: 'Copy' }))
      await screen.findByText('Copied 1 stream.')
      put.mockClear()
      await user.click(screen.getByRole('button', { name: 'Paste' }))
      expect(put).toHaveBeenCalledWith('p', expect.objectContaining({ added: [stream('a')], added_sidecar: [doc] }))
    })

    it('a delete queued behind a pending toggle leaves no undo entry and clears the history', async () => {
      const { put, user } = await open(pageOf([stream('a'), stream('b'), stream('c')]), pageOf([stream('a'), stream('b'), stream('c')], { rev: 'r1' }))
      put.mockResolvedValueOnce(reply({ rev: 'r1', stream_count: 3 }))
      await user.click(screen.getByRole('checkbox', { name: 'Enabled a' }))
      await vi.waitFor(() => expect(undoBtn()).toHaveAttribute('title'))
      let answer: (r: DiffReply) => void = () => undefined
      put.mockImplementationOnce(() => new Promise<DiffReply>((resolve) => { answer = resolve }))
      await user.click(screen.getByRole('checkbox', { name: 'Enabled b' }))
      await user.click(nameCell('b'))
      await user.click(screen.getByRole('button', { name: 'Delete' }))
      put.mockResolvedValueOnce(reply({ rev: 'r3', stream_count: 2 }))
      await user.click(screen.getByRole('button', { name: 'Confirm delete' }))
      answer(reply({ rev: 'r2', stream_count: 3 }))
      await vi.waitFor(() => expect(put).toHaveBeenCalledTimes(3))
      await vi.waitFor(() => expect(undoBtn()).toBeDisabled())
      expect(undoBtn()).not.toHaveAttribute('title')       // not merely disabled: the history is empty
    })

    it('two quick renames of one row leave no undo entry', async () => {
      const { put, user } = await open(pageOf([stream('a'), stream('b')]))
      let answer: (r: DiffReply) => void = () => undefined
      put.mockImplementationOnce(() => new Promise<DiffReply>((resolve) => { answer = resolve }))
      await user.dblClick(nameCell('a'))
      await user.type(screen.getByRole('textbox', { name: 'Name of stream 0' }), 'x{Enter}')
      expect(put).toHaveBeenCalledTimes(1)
      put.mockResolvedValueOnce(reply({ rev: 'r2', stream_count: 2 }))
      await user.dblClick(nameCell('a'))
      await user.type(screen.getByRole('textbox', { name: 'Name of stream 0' }), 'y{Enter}')
      answer(reply({ rev: 'r1', stream_count: 2 }))
      await vi.waitFor(() => expect(put).toHaveBeenCalledTimes(2))
      await vi.waitFor(() => expect(undoBtn()).toBeDisabled())
      expect(undoBtn()).not.toHaveAttribute('title')
    })

    it('copy and duplicate say when an editor document could not be kept', async () => {
      vi.spyOn(api, 'streamSidecar').mockRejectedValue(new Error('gone'))
      const { put, user } = await open(withEditor(pageOf([stream('a'), stream('b')]), 0))
      put.mockResolvedValue(reply({ stream_count: 3 }))
      await user.click(nameCell('a'))
      await user.click(screen.getByRole('button', { name: 'Copy' }))
      expect(await screen.findByRole('status')).toHaveTextContent('Copied 1 stream. (editor documents not copied)')
      await user.click(screen.getByRole('button', { name: 'Duplicate' }))
      await vi.waitFor(() => expect(put).toHaveBeenCalled())
      expect(put.mock.calls[0][1]).not.toHaveProperty('added_sidecar')
      expect(await screen.findByRole('status')).toHaveTextContent('(editor documents not copied)')
    })

    it('Copy during an open delete confirm leaves Add stream blocked', async () => {
      vi.spyOn(api, 'streamSidecar').mockResolvedValue({ rev: 'r0', entry: doc })
      const { user } = await open(withEditor(pageOf([stream('a'), stream('b')]), 0))
      await user.click(nameCell('a'))
      await user.click(screen.getByRole('button', { name: 'Delete' }))
      expect(screen.getByRole('button', { name: 'Add stream' })).toBeDisabled()
      await user.click(screen.getByRole('button', { name: 'Copy' }))
      await screen.findByText('Copied 1 stream.')
      expect(screen.getByRole('group', { name: 'Delete streams' })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Add stream' })).toBeDisabled()
    })
  })
})
