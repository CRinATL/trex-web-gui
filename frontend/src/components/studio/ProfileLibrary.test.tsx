import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import type { LibraryEntry, TestSetup } from '../../api'
import * as state from '../../state'
import { ProfileLibrary } from './ProfileLibrary'

afterEach(() => vi.restoreAllMocks())

const entry = (slug: string, o: Partial<LibraryEntry> = {}): LibraryEntry => ({
  slug, name: slug, path: `/lib/${slug}.json`, stream_count: 2, created: 'c', updated: 'u', source_missing: false, load_error: null,
  source: { kind: 'json', path: null, args: [], tunables: null, ports_differ: false, imported: 'i' }, ...o,
})
const setupWith = (path: string): TestSetup => ({
  name: 'S', slug: 's', created: '', updated: '', server: null, dashboard: null,
  ports: [{ key: 'k', port: 0, profile: { path, kind: 'json' }, tunables: { values: {}, extra: '' }, mult: '1', duration: -1, force: false, acquire_force: false, stop_first: false, layer: null }],
})
function show(entries: LibraryEntry[], o: { selected?: string | null; loadErrors?: { file: string; error: string }[]; setups?: TestSetup[] } = {}) {
  vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, setups: o.setups ?? [] })
  const onSelect = vi.fn(), onChanged = vi.fn()
  const r = render(<ProfileLibrary entries={entries} loadErrors={o.loadErrors ?? []} selected={o.selected ?? null} onSelect={onSelect} onChanged={onChanged} />)
  return { onSelect, onChanged, ...r }
}
const name = () => screen.getByRole('textbox', { name: 'Name' })

describe('ProfileLibrary', () => {
  it('lists entries with count, kind and problems as text', () => {
    show([entry('a', { stream_count: 3, source: { kind: 'py', path: '/x.py', args: [], tunables: null, ports_differ: false, imported: 'i' }, source_missing: true }),
      entry('b', { stream_count: 1, load_error: 'sidecar: bad' }), entry('c')], { loadErrors: [{ file: 'junk.json', error: 'not a profile' }] })
    const opts = screen.getAllByRole('option').map((o) => o.textContent)
    expect(opts[0]).toBe('a 3 streams · py · source missing')
    expect(opts[1]).toBe('b 1 stream · json · sidecar: bad')
    expect(opts[2]).toBe('c 2 streams · json')
    expect(opts[3]).toBe('junk.json: not a profile')
    expect(screen.getByRole('listbox', { name: 'Profiles' })).toBeInTheDocument()
  })

  it('New creates and selects the entry', async () => {
    const create = vi.spyOn(api, 'createProfile').mockResolvedValue(entry('fresh'))
    const { onChanged } = show([])
    await userEvent.click(screen.getByRole('button', { name: 'New…' }))
    await userEvent.type(name(), 'Fresh')
    await userEvent.click(screen.getByRole('button', { name: 'Create' }))
    expect(create).toHaveBeenCalledWith('Fresh')
    expect(onChanged).toHaveBeenCalledWith('fresh')
    expect(screen.queryByRole('textbox', { name: 'Name' })).toBeNull()
  })

  it('a duplicate name shows the 409 message inline', async () => {
    vi.spyOn(api, 'createProfile').mockRejectedValue(new api.ApiError(409, 'exists', 'A profile Fresh already exists'))
    const { onChanged } = show([entry('fresh')])
    await userEvent.click(screen.getByRole('button', { name: 'New…' }))
    await userEvent.type(name(), 'Fresh')
    await userEvent.click(screen.getByRole('button', { name: 'Create' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('A profile Fresh already exists')
    expect(onChanged).not.toHaveBeenCalled()
    expect(name()).toHaveValue('Fresh')     // the field stays open for another name
  })

  it('Cancel or Escape on the name field clears its error', async () => {
    vi.spyOn(api, 'createProfile').mockRejectedValue(new api.ApiError(409, 'exists', 'A profile Fresh already exists'))
    show([entry('fresh')])
    for (const close of [() => userEvent.click(screen.getByRole('button', { name: 'Cancel' })), () => userEvent.type(name(), '{Escape}')]) {
      await userEvent.click(screen.getByRole('button', { name: 'New…' }))
      await userEvent.type(name(), 'Fresh')
      await userEvent.click(screen.getByRole('button', { name: 'Create' }))
      expect(await screen.findByRole('alert')).toHaveTextContent('A profile Fresh already exists')
      await close()
      expect(screen.queryByRole('textbox', { name: 'Name' })).toBeNull()
      expect(screen.queryByRole('alert')).toBeNull()
    }
  })

  it('Delete asks inline, then deletes', async () => {
    const del = vi.spyOn(api, 'deleteProfile').mockResolvedValue({ profiles: [], load_errors: [], dir: '/lib' })
    const { onChanged } = show([entry('a')], { selected: 'a' })
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
    expect(del).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('Delete profile a?')
    await userEvent.click(screen.getByRole('button', { name: 'Confirm delete' }))
    expect(del).toHaveBeenCalledWith('a')
    expect(onChanged).toHaveBeenCalledWith(null)
  })

  it('Cancel on the delete question deletes nothing', async () => {
    const del = vi.spyOn(api, 'deleteProfile')
    show([entry('a')], { selected: 'a' })
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }))
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('alert')).toBeNull()
    expect(del).not.toHaveBeenCalled()
  })

  it('Rename sends the rev just read and follows the new slug', async () => {
    vi.spyOn(api, 'getProfile').mockResolvedValue({ entry: entry('a'), rev: 'r1', sidecar: {} as api.Sidecar })
    const rename = vi.spyOn(api, 'renameProfile').mockResolvedValue(entry('b'))
    const { onChanged } = show([entry('a')], { selected: 'a' })
    await userEvent.click(screen.getByRole('button', { name: 'Rename…' }))
    await userEvent.clear(name()); await userEvent.type(name(), 'b')
    await userEvent.click(screen.getByRole('button', { name: 'Rename' }))
    expect(rename).toHaveBeenCalledWith('a', 'b', 'r1')
    expect(onChanged).toHaveBeenCalledWith('b')
  })

  it('Duplicate sends the new name', async () => {
    const dup = vi.spyOn(api, 'duplicateProfile').mockResolvedValue(entry('a-copy'))
    const { onChanged } = show([entry('a')], { selected: 'a' })
    await userEvent.click(screen.getByRole('button', { name: 'Duplicate…' }))
    expect(name()).toHaveValue('a copy')
    await userEvent.click(screen.getByRole('button', { name: 'Duplicate' }))
    expect(dup).toHaveBeenCalledWith('a', 'a copy')
    expect(onChanged).toHaveBeenCalledWith('a-copy')
  })

  it('Rename warns when a test setup points at the entry', async () => {
    show([entry('a'), entry('b')], { selected: 'a', setups: [setupWith('/lib/a.json'), setupWith('/lib/b.json')] })
    await userEvent.click(screen.getByRole('button', { name: 'Rename…' }))
    expect(screen.getByText('1 test setup points at this profile; they will need the new path.')).toBeInTheDocument()
  })

  it('Rename does not warn when no test setup points at the entry', async () => {
    show([entry('a')], { selected: 'a', setups: [setupWith('/lib/other.json')] })
    await userEvent.click(screen.getByRole('button', { name: 'Rename…' }))
    expect(screen.queryByText(/test setup/)).toBeNull()
  })

  it('arrow keys move the selection', async () => {
    const { onSelect, rerender, onChanged } = show([entry('a'), entry('b'), entry('c')], { selected: 'a' })
    const view = (sel: string) => rerender(<ProfileLibrary entries={[entry('a'), entry('b'), entry('c')]} loadErrors={[]} selected={sel} onSelect={onSelect} onChanged={onChanged} />)
    screen.getAllByRole('option')[0].focus()
    await userEvent.keyboard('{ArrowDown}')
    expect(onSelect).toHaveBeenLastCalledWith('b'); view('b')
    await userEvent.keyboard('{End}')
    expect(onSelect).toHaveBeenLastCalledWith('c'); view('c')
    await userEvent.keyboard('{ArrowDown}')       // clamped at the end: no new call
    expect(onSelect).toHaveBeenCalledTimes(2)
    await userEvent.keyboard('{ArrowUp}')
    expect(onSelect).toHaveBeenLastCalledWith('b'); view('b')
    await userEvent.keyboard('{Home}')
    expect(onSelect).toHaveBeenLastCalledWith('a')
  })

  it('clicking an option selects it', async () => {
    const { onSelect } = show([entry('a'), entry('b')])
    await userEvent.click(screen.getAllByRole('option')[1])
    expect(onSelect).toHaveBeenCalledWith('b')
  })

  it('Import… opens the import dialog and selects the imported entry', async () => {
    const imp = vi.spyOn(api, 'importProfile').mockResolvedValue(entry('udp-1pkt'))
    const { onChanged } = show([entry('a')])
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Import…' }))
    await user.type(screen.getByRole('textbox', { name: 'Path' }), '/srv/udp_1pkt.json')
    await user.click(screen.getByRole('button', { name: 'Import' }))
    expect(imp).toHaveBeenCalledWith({ path: '/srv/udp_1pkt.json', kind: 'json', name: 'udp_1pkt' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(onChanged).toHaveBeenCalledWith('udp-1pkt')
  })

  it('Use in Control… opens the dialog for the selected entry', async () => {
    vi.spyOn(state, 'useAppState').mockReturnValue({ ...state.initialState, setups: [] })
    render(<MemoryRouter><ProfileLibrary entries={[entry('a')]} loadErrors={[]} selected="a" onSelect={vi.fn()} onChanged={vi.fn()} /></MemoryRouter>)
    await userEvent.click(screen.getByRole('button', { name: 'Use in Control…' }))
    expect(screen.getByRole('dialog', { name: 'Use in Control' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog', { name: 'Use in Control' })).toBeNull()
  })
})
