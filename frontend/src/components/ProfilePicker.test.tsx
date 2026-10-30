import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api'
import { resetHostCache } from '../useHost'
import { ProfilePicker } from './ProfilePicker'

const listing = (path: string, parent: string | null, entries: api.FsEntry[], truncated = false): api.FsList => ({ path, parent, entries, truncated })
const dir = (name: string): api.FsEntry => ({ name, dir: true, size: null })
const file = (name: string): api.FsEntry => ({ name, dir: false, size: 120 })

function Harness({ onInspected = vi.fn() }: { onInspected?: (i: api.Inspected | null) => void }) {
  const [path, setPath] = useState('')
  return <ProfilePicker portId={2} path={path} onPath={setPath} onInspected={onInspected} />
}
beforeEach(() => { localStorage.clear(); resetHostCache() })
afterEach(() => vi.restoreAllMocks())

describe('ProfilePicker', () => {
  const about = (host: string): api.About => ({ app_version: '1', trex_pin: { upstream_sha: 'x', patches: [] }, supported: 's', host })
  it('the help is behind ⓘ and names the trex-web-gui host', async () => {
    vi.spyOn(api, 'getAbout').mockResolvedValue(about('trex-host'))
    render(<Harness />)
    const paths = await screen.findByText('Paths are on trex-host, the computer running trex-web-gui serve, not the one your browser is on. Nothing is uploaded.')
    const run = screen.getByText('.py profiles run as code on trex-host.')
    expect(paths).not.toBeVisible()
    expect(run).not.toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'About profile paths' }))
    expect(paths).toBeVisible()
    expect(run).toBeVisible()
  })
  it('while the host is unknown the help names the computer running trex-web-gui serve', async () => {
    vi.spyOn(api, 'getAbout').mockReturnValue(new Promise(() => {}))
    render(<Harness />)
    expect(screen.getByText('Paths are on the computer running trex-web-gui serve, not the one your browser is on. Nothing is uploaded.')).toBeInTheDocument()
    expect(screen.getByText('.py profiles run as code there.')).toBeInTheDocument()
  })
  it('a failed host lookup keeps the fallback and throws nothing', async () => {
    const get = vi.spyOn(api, 'getAbout').mockRejectedValue(new Error('down'))
    render(<Harness />)
    await vi.waitFor(() => expect(get).toHaveBeenCalled())
    await act(async () => { await Promise.resolve(); await Promise.resolve() })   // let the rejection settle
    expect(screen.getByText('.py profiles run as code there.')).toBeInTheDocument()
  })
  it('Inspect sends the path and this port and lifts the result', async () => {
    const result: api.Inspected = { path: '/p/a.py', kind: 'py', tunables: [], summary: [], stream_count: 0 }
    const insp = vi.spyOn(api, 'inspect').mockResolvedValue(result)
    const onInspected = vi.fn()
    render(<Harness onInspected={onInspected} />)
    expect(screen.getByRole('button', { name: 'Inspect' })).toBeDisabled()
    await userEvent.type(screen.getByRole('textbox', { name: /Profile path/ }), '/p/a.py')
    await userEvent.click(screen.getByRole('button', { name: 'Inspect' }))
    expect(insp).toHaveBeenCalledWith({ path: '/p/a.py', ports: [2], tunables: [] })
    expect(onInspected).toHaveBeenCalledWith(result)
  })
  it('a non-422 failure is a plain alert and clears the lifted result', async () => {
    vi.spyOn(api, 'inspect').mockRejectedValue(new api.ApiError(404, 'not_found', 'no such file'))
    const onInspected = vi.fn()
    render(<Harness onInspected={onInspected} />)
    await userEvent.type(screen.getByRole('textbox', { name: /Profile path/ }), '/nope.py')
    await userEvent.click(screen.getByRole('button', { name: 'Inspect' }))
    const el = await screen.findByRole('alert')
    expect(el.tagName).toBe('P')
    expect(el).toHaveTextContent('no such file')
    expect(onInspected).toHaveBeenLastCalledWith(null)
  })
  it('browses: directories enter, Up goes to the parent, a file picks its full path, and the directory is remembered', async () => {
    const list = vi.spyOn(api, 'fsList').mockImplementation(async (p) =>
      p === '/home/me/sub' ? listing('/home/me/sub', '/home/me', [file('udp.py')]) : listing('/home/me', '/home', [dir('sub'), file('top.py')]))
    render(<Harness />)
    await userEvent.click(screen.getByRole('button', { name: 'Browse…' }))
    const dlg = await screen.findByRole('dialog')
    expect(await screen.findByText('/home/me')).toBeInTheDocument()
    expect(list).toHaveBeenCalledWith()
    await userEvent.click(await screen.findByRole('button', { name: 'sub/' }))
    expect(await screen.findByText('/home/me/sub')).toBeInTheDocument()
    expect(localStorage.getItem('trex-web-gui.profileDir')).toBe('/home/me/sub')
    await userEvent.click(screen.getByRole('button', { name: 'Up' }))
    expect(await screen.findByText('/home/me')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'sub/' }))
    await userEvent.click(await screen.findByRole('button', { name: 'udp.py' }))
    expect(dlg).not.toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: /Profile path/ })).toHaveValue('/home/me/sub/udp.py')
  })
  it('opens at the remembered directory', async () => {
    localStorage.setItem('trex-web-gui.profileDir', '/home/me/sub')
    const list = vi.spyOn(api, 'fsList').mockResolvedValue(listing('/home/me/sub', '/home/me', []))
    render(<Harness />)
    await userEvent.click(screen.getByRole('button', { name: 'Browse…' }))
    await screen.findByText('No profiles or directories here.')
    expect(list).toHaveBeenCalledWith('/home/me/sub')
  })
  it('falls back to the default directory when the remembered one is gone', async () => {
    localStorage.setItem('trex-web-gui.profileDir', '/gone')
    vi.spyOn(api, 'fsList').mockImplementation(async (p) => { if (p === '/gone') throw new api.ApiError(404, 'not_found', '/gone is not a directory'); return listing('/home/me', '/home', []) })
    render(<Harness />)
    await userEvent.click(screen.getByRole('button', { name: 'Browse…' }))
    expect(await screen.findByText('/home/me')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
  })
  it('a listing error is an alert, and Escape closes the dialog and returns focus', async () => {
    vi.spyOn(api, 'fsList').mockRejectedValue(new api.ApiError(403, 'forbidden', 'permission denied'))
    render(<Harness />)
    const open = screen.getByRole('button', { name: 'Browse…' })
    open.focus()
    await userEvent.click(open)
    expect(await screen.findByRole('alert')).toHaveTextContent('permission denied')
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(open).toHaveFocus()
  })
  it('works when localStorage throws', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied') })
    vi.spyOn(api, 'fsList').mockResolvedValue(listing('/home/me', null, [file('a.py')]))
    render(<Harness />)
    await userEvent.click(screen.getByRole('button', { name: 'Browse…' }))
    expect(await screen.findByRole('button', { name: 'a.py' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Up' })).toBeDisabled()
  })
  it('says when the listing was truncated', async () => {
    vi.spyOn(api, 'fsList').mockResolvedValue(listing('/home/me', null, [file('a.py')], true))
    render(<Harness />)
    await userEvent.click(screen.getByRole('button', { name: 'Browse…' }))
    expect(await screen.findByText(/truncated/)).toBeInTheDocument()
  })
  it('the Browse footer holds Cancel in .end', async () => {
    vi.spyOn(api, 'fsList').mockResolvedValue(listing('/home/me', null, []))
    render(<Harness />)
    await userEvent.click(screen.getByRole('button', { name: 'Browse…' }))
    const dlg = screen.getByRole('dialog', { name: 'Browse for a profile' })
    expect([...dlg.querySelectorAll('.dialog-footer .end button')].map((b) => b.textContent)).toEqual(['Cancel'])
  })
  describe('Browse picks', () => {
    const insp = (path: string): api.Inspected => ({ path, kind: 'yaml', tunables: [], summary: [], stream_count: 0 })
    async function pick(name: string) {
      vi.spyOn(api, 'fsList').mockResolvedValue(listing('/home/me', null, [file(name)]))
      await userEvent.click(screen.getByRole('button', { name: 'Browse…' }))
      await userEvent.click(await screen.findByRole('button', { name }))
    }
    it('picking a .yaml file in Browse inspects it', async () => {
      const spy = vi.spyOn(api, 'inspect').mockResolvedValue(insp('/home/me/a.YAML'))
      const onInspected = vi.fn()
      render(<Harness onInspected={onInspected} />)
      await pick('a.YAML')
      expect(spy).toHaveBeenCalledWith({ path: '/home/me/a.YAML', ports: [2], tunables: [] })
      await vi.waitFor(() => expect(onInspected).toHaveBeenCalled())
    })
    it('picking a .py file only fills the path', async () => {
      const spy = vi.spyOn(api, 'inspect')
      render(<Harness />)
      await pick('a.py')
      expect(screen.getByRole('textbox', { name: /Profile path/ })).toHaveValue('/home/me/a.py')
      expect(spy).not.toHaveBeenCalled()
    })
  })
  it('Library… picks a json entry and inspects it', async () => {
    const entry = { slug: 'a', name: 'a', path: '/lib/a.json', stream_count: 1, created: 'c', updated: 'u', source_missing: false, load_error: null,
      source: { kind: 'json' as const, path: null, args: [], tunables: null, ports_differ: false, imported: 'i' } }
    vi.spyOn(api, 'listLibrary').mockResolvedValue({ profiles: [entry], load_errors: [], dir: '/lib' })
    const result: api.Inspected = { path: '/lib/a.json', kind: 'json', tunables: [], summary: [], stream_count: 1 }
    const insp = vi.spyOn(api, 'inspect').mockResolvedValue(result)
    const onInspected = vi.fn()
    render(<Harness onInspected={onInspected} />)
    await userEvent.click(screen.getByRole('button', { name: 'Library…' }))
    await userEvent.click(await screen.findByRole('button', { name: 'a' }))
    expect(screen.getByRole('textbox', { name: /Profile path/ })).toHaveValue('/lib/a.json')
    expect(insp).toHaveBeenCalledWith({ path: '/lib/a.json', ports: [2], tunables: [] })
    expect(onInspected).toHaveBeenCalledWith(result)
    expect(screen.queryByRole('dialog', { name: 'Pick from the library' })).toBeNull()
  })
})
