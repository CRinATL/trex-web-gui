import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiError, type Bundle, type BundlePreview } from '../../api'
import fixture from '../../__fixtures__/bundlePreview.json'
import { BundleImportDialog } from './BundleImportDialog'

const bundle: Bundle = { format: 'trex-web-gui-settings', version: 1, exported_at: '2026-10-21T10:00:00Z', app_version: '0.9', sections: {} }
// the preview the backend really sent (tests/contract/test_wire_fixtures.py), with the theme section absent
const recorded = (): BundlePreview => structuredClone(fixture.preview) as BundlePreview
const setups = (p: BundlePreview) => p.sections.test_setups

function show(p: BundlePreview, onApply = vi.fn().mockResolvedValue(undefined)) {
  render(<BundleImportDialog bundle={bundle} preview={p} onApply={onApply} onClose={() => undefined} />)
  return onApply
}

describe('BundleImportDialog', () => {
  it('rows from bundlePreview.json with per-section modes', () => {
    show(recorded())
    for (const name of ['Preferences', 'Connections', 'Port labels', 'Test setups']) expect(screen.getByRole('row', { name: new RegExp(`^${name}`) })).toBeInTheDocument()
    expect(screen.getAllByRole('columnheader').map((h) => h.textContent)).toEqual(['Section', 'Mode', 'What happens'])
    expect(screen.getByRole('combobox', { name: 'Preferences mode' })).toHaveValue('skip')
    for (const name of ['Connections', 'Port labels', 'Test setups']) expect(screen.getByRole('combobox', { name: `${name} mode` })).toHaveValue('merge')
    expect(within(screen.getByRole('combobox', { name: 'Preferences mode' })).getAllByRole('option').map((o) => o.textContent)).toEqual(['replace', 'skip'])
    expect(within(screen.getByRole('combobox', { name: 'Test setups mode' })).getAllByRole('option').map((o) => o.textContent)).toEqual(['replace', 'merge', 'skip'])
    const items = within(screen.getByRole('list', { name: 'Test setups items' })).getAllByRole('listitem')
    expect(items.map((li) => li.textContent)).toEqual(['lab-b: added', 'lab-a: overwritten', 'old: kept (on this GUI, not in the file; replace would delete it)'])
    // the per-setup lines are shown verbatim
    expect(screen.getByText('lab-a: port 0 force-acquires on Run')).toBeInTheDocument()
    expect(screen.queryByText(/also apply theme/i)).not.toBeInTheDocument()
  })
  it('the preview flags a changed wireshark_path and skips preferences by default', async () => {  // Review Focus 10
    const onApply = show(recorded())
    expect(screen.getByText('`wireshark_path` changes from `wireshark` to `/opt/ws`')).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Preferences mode' })).toHaveValue('skip')
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }))
    expect(onApply.mock.calls[0][0]).toMatchObject({ preferences: 'skip' })
  })
  it('Apply disabled while a section is invalid', async () => {
    const p = recorded()
    setups(p).valid = false; setups(p).errors = ['test_setups: merge would leave 501 setups here; at most 500']
    show(p)
    expect(screen.getByText(/at most 500/)).toBeInTheDocument()
    expect(screen.getByText(/test_setups: merge would leave 501 setups here/)).toBeInTheDocument()   // the section's error renders
    expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled()
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Test setups mode' }), 'skip')
    expect(screen.getByRole('button', { name: 'Apply' })).toBeEnabled()
  })
  it('apply sends the chosen modes', async () => {
    const onApply = show(recorded())
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Preferences mode' }), 'replace')
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Connections mode' }), 'replace')
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Port labels mode' }), 'skip')
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }))
    expect(onApply).toHaveBeenCalledWith({ preferences: 'replace', connections: 'replace', port_labels: 'skip', test_setups: 'merge', theme: 'skip' })
  })
  it('the preview lists each re-pointed setup path', async () => {  // Review Focus 13
    const p = recorded()
    p.path_changes.push({ slug: 'lab-b', port: 1, from: '/a.py', to: '/b.py' })
    show(p)
    await userEvent.click(screen.getByText(/Before you Load these/))
    const list = screen.getByRole('list', { name: 'Re-pointed setups' })
    expect(within(list).getAllByRole('listitem')).toHaveLength(2)
    expect(list).toHaveTextContent('lab-a, port 0: /old/other.py becomes /p/udp.py')
    expect(list).toHaveTextContent('lab-b, port 1: /a.py becomes /b.py')
    expect(within(screen.getByRole('list', { name: 'Profile paths' })).getByText('/p/udp.py')).toBeInTheDocument()
  })
  it('replace shows what it removes', async () => {  // Review Focus 14
    show(recorded())
    expect(screen.getByText('old: kept (on this GUI, not in the file; replace would delete it)')).toBeInTheDocument()
    expect(screen.queryByText('old: deleted')).not.toBeInTheDocument()
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Test setups mode' }), 'replace')
    expect(screen.getByText('old: deleted')).toBeInTheDocument()
  })
  it('items are named in every mode with what the mode does to them', async () => {
    show(recorded())
    const lines = () => within(screen.getByRole('list', { name: 'Test setups items' })).getAllByRole('listitem').map((li) => li.textContent)
    const select = screen.getByRole('combobox', { name: 'Test setups mode' })
    expect(lines()).toEqual(['lab-b: added', 'lab-a: overwritten', 'old: kept (on this GUI, not in the file; replace would delete it)'])
    await userEvent.selectOptions(select, 'replace')
    expect(lines()).toEqual(['lab-b: added', 'lab-a: overwritten', 'old: deleted'])
    await userEvent.selectOptions(select, 'skip')
    expect(lines()).toEqual(['lab-b: not added', 'lab-a: not overwritten', 'old: kept'])
  })
  it('a section with no differences says nothing to do', () => {
    show(recorded())
    expect(within(screen.getByRole('row', { name: /^Port labels/ })).getByText('same as here, nothing to do')).toBeInTheDocument()
  })
  it('"only here" no longer appears', async () => {
    show(recorded())
    for (const m of ['replace', 'skip', 'merge']) {
      await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Test setups mode' }), m)
      expect(screen.queryByText(/only here/)).not.toBeInTheDocument()
    }
  })
  it('preferences flags are prefixed by what the mode does', async () => {
    show(recorded())
    const row = screen.getByRole('row', { name: /^Preferences/ })
    expect(within(row).getByText(/skipped:/)).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Preferences mode' }), 'replace')
    // the backend line already says "changes from", so replace adds no verb of its own
    expect(within(row).queryByText(/will change/)).not.toBeInTheDocument()
    expect(within(row).queryByText(/skipped:/)).not.toBeInTheDocument()
  })
  it('the test-setups flags and paths sit in a fold that names its counts', () => {
    show(recorded())
    const fold = screen.getByText('Before you Load these (1 profiles, 1 re-pointed)').closest('details')!
    expect(within(fold).getByText('lab-a: port 0 force-acquires on Run')).toBeInTheDocument()
    expect(within(fold).getByRole('list', { name: 'Profile paths' })).toBeInTheDocument()
  })
  it('Apply stays visible while the body scrolls', () => {
    show(recorded())
    const body = document.querySelector('.bundle-body')!
    const apply = screen.getByRole('button', { name: 'Apply' })
    expect(body.contains(apply)).toBe(false)
    expect(apply.closest('.dialog-footer')).not.toBeNull()
    expect(screen.getByText('Switching to replace deletes what is kept')).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toHaveClass('bundle-import')
  })
  it('offers the theme only when its section is valid and passes the choice on', async () => {
    const p = recorded()
    p.sections.theme = { present: true, valid: true, errors: [], adds: [], changes: [], removes: [], flags: [] }
    const onApply = show(p)
    await userEvent.click(screen.getByRole('checkbox', { name: 'Also apply theme' }))
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }))
    expect(onApply.mock.calls[0][0]).toMatchObject({ theme: 'replace' })
  })
  it('an invalid theme is shown and cannot be applied', () => {
    const p = recorded()
    p.sections.theme = { present: true, valid: false, errors: ["theme: 'neon' is not one of system"], adds: [], changes: [], removes: [], flags: [] }
    show(p)
    expect(screen.getByText(/'neon' is not one of/)).toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: 'Also apply theme' })).not.toBeInTheDocument()
  })
  it('a storage_error names what was written and what failed', async () => {  // R9
    const err = new ApiError(500, 'storage_error', 'could not write test_setups/lab-b', { written: ['preferences+connections', 'test_setups/lab-a'], failed: 'test_setups/lab-b' })
    show(recorded(), vi.fn().mockRejectedValue(err))
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }))
    expect(await screen.findByText('Already written: preferences+connections, test_setups/lab-a')).toBeInTheDocument()
    expect(screen.getByText('Failed: test_setups/lab-b')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Apply' })).toBeEnabled()
  })
  it('Escape is ignored while an Apply is in flight, so its storage_error is still shown', async () => {
    let reject!: (e: Error) => void
    const onClose = vi.fn()
    const onApply = vi.fn().mockReturnValue(new Promise<void>((_, r) => { reject = r }))
    render(<BundleImportDialog bundle={bundle} preview={recorded()} onApply={onApply} onClose={onClose} />)
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }))
    await userEvent.keyboard('{Escape}')
    expect(onClose).not.toHaveBeenCalled()
    await act(async () => { reject(new ApiError(500, 'storage_error', 'disk full', { written: ['port_labels'], failed: 'test_setups/lab-b' })) })
    expect(await screen.findByText('Failed: test_setups/lab-b')).toBeInTheDocument()
    await userEvent.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledTimes(1)
  })
  it('header fields from the file that are not strings are not rendered and do not crash', () => {
    const odd = { ...bundle, exported_at: {}, app_version: [1] } as unknown as Bundle
    render(<BundleImportDialog bundle={odd} preview={recorded()} onApply={vi.fn()} onClose={() => undefined} />)
    expect(screen.getByText(/Exported at an unknown time by trex-web-gui \?\./)).toBeInTheDocument()
  })
  it('a very long header string is cut short', () => {
    render(<BundleImportDialog bundle={{ ...bundle, app_version: 'x'.repeat(500) }} preview={recorded()} onApply={vi.fn()} onClose={() => undefined} />)
    expect(screen.getByText(/x{60}…/)).toBeInTheDocument()
    expect(screen.queryByText(/x{61}/)).not.toBeInTheDocument()
  })
  it('a section that failed to parse does not claim to be the same', () => {
    const p = recorded()
    p.sections.connections = { present: true, valid: false, errors: ['connections: not a list'], adds: [], changes: [], removes: [], flags: [] }
    show(p)
    expect(within(screen.getByRole('row', { name: /^Connections/ })).queryByText('same as here, nothing to do')).not.toBeInTheDocument()
  })
  it('preferences with only flags: no nothing-to-do, no empty items list, flags keep their prefix', () => {
    show(recorded())
    const row = screen.getByRole('row', { name: /^Preferences/ })
    expect(within(row).queryByText('same as here, nothing to do')).not.toBeInTheDocument()
    expect(screen.queryByRole('list', { name: 'Preferences items' })).not.toBeInTheDocument()
    expect(within(row).getByText(/skipped:/)).toBeInTheDocument()
  })
  it('the replace warning shows only while something kept would be deleted', async () => {
    const warn = 'Switching to replace deletes what is kept'
    show(recorded())
    expect(screen.getByText(warn)).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Test setups mode' }), 'replace')
    expect(screen.queryByText(warn)).not.toBeInTheDocument()
  })
  it('the replace warning is absent when no section has removes', () => {
    const p = recorded()
    setups(p).removes = []
    show(p)
    expect(screen.queryByText('Switching to replace deletes what is kept')).not.toBeInTheDocument()
  })
  it('the table headers are column headers', () => {
    show(recorded())
    for (const h of screen.getAllByRole('columnheader')) expect(h).toHaveAttribute('scope', 'col')
  })
})
