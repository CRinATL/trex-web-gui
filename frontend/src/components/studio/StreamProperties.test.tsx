import { act, cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { StreamJson, StreamPage } from '../../api'
import streams from '../../__fixtures__/studioStreams.json'
import { StreamProperties } from './StreamProperties'

afterEach(cleanup)
const stored = (streams.udp_for_benchmarks as unknown as StreamPage).rows[0].stream
const burst: StreamJson = { ...stored, name: 'b', mode: { type: 'multi_burst', rate: { type: 'pps', value: 10 }, pkts_per_burst: 5, ibg: 100, count: 3 } }

function show(stream: StreamJson = stored, over: Partial<Parameters<typeof StreamProperties>[0]> = {}) {
  const props = { stream, index: 1, names: [null, stream.name ?? null, 'other'] as (string | null)[], onSave: vi.fn(), onPrev: vi.fn(), onNext: vi.fn(), ...over }
  const view = render(<StreamProperties {...props} />)
  return { ...props, user: userEvent.setup(), view }
}
const save = () => screen.getByRole('button', { name: 'Save' })

describe('StreamProperties', () => {
  it('Save sends only the changed fields', async () => {
    const { onSave, user } = show()
    expect(save()).toBeDisabled()               // nothing changed
    await user.clear(screen.getByLabelText('ISG (µs)'))
    await user.type(screen.getByLabelText('ISG (µs)'), '25')
    await user.click(screen.getByLabelText('Start paused'))
    await user.selectOptions(screen.getByLabelText('Destination MAC'), 'By ARP')
    await user.click(save())
    expect(onSave).toHaveBeenCalledTimes(1)
    expect(onSave).toHaveBeenCalledWith({ isg: 25, start_paused: true, flags: 4 })
  })

  it('a mode change shows the fields of its mode', async () => {
    const { user } = show()
    expect(screen.queryByLabelText('Packets')).not.toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Mode'), 'Single burst')
    expect(screen.getByLabelText('Packets')).toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Mode'), 'Multi-burst')
    for (const l of ['Packets per burst', 'Bursts', 'IBG (µs)']) expect(screen.getByLabelText(l)).toBeInTheDocument()
    expect(screen.queryByLabelText('Packets')).not.toBeInTheDocument()
  })

  it('Save is disabled while a field is invalid and says why', async () => {
    const { user } = show(burst)
    await user.clear(screen.getByLabelText('Bursts'))
    await user.type(screen.getByLabelText('Bursts'), '0')
    expect(save()).toBeDisabled()
    const field = screen.getByLabelText('Bursts')
    expect(field).toBeInvalid()
    expect(screen.getByText('Enter a whole number, at least 1.')).toBeInTheDocument()
    expect(field).toHaveAccessibleDescription('Enter a whole number, at least 1. edited')   // edited too (RC1-31)
    await user.clear(field)
    await user.type(field, '4')
    expect(save()).toBeEnabled()
    expect(screen.queryByText('Enter a whole number, at least 1.')).not.toBeInTheDocument()
  })

  it('Next is disabled for a continuous stream', async () => {
    const { user } = show(burst)
    const next = screen.getByLabelText('Next')
    expect(next).toBeEnabled()
    expect(Array.from((next as HTMLSelectElement).options).map((o) => o.text)).toEqual(['Stop', 'other'])   // not itself, not unnamed
    await user.selectOptions(screen.getByLabelText('Mode'), 'Continuous')
    expect(next).toBeDisabled()
    cleanup()
    show(stored)
    expect(screen.getByLabelText('Next')).toBeDisabled()
  })

  it('VXLAN and IEEE 1588 are read-only with the note', async () => {
    const s: StreamJson = { ...stored, flow_stats: { enabled: true, rule_type: 'latency', stream_id: 3, vxlan: false, ieee_1588: true } }
    const { user, onSave } = show(s, { names: [null] })
    expect(screen.getByText('IEEE 1588: yes (as imported)')).toBeInTheDocument()
    expect(screen.getByText('VXLAN: no (as imported)')).toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: /VXLAN|1588/ })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'About VXLAN and IEEE 1588' }))
    const note = screen.getByText(/drops both at import and at assign/)
    expect(note).toBeVisible()
    expect(note).toHaveTextContent(/never reach the server/)
    // an edit elsewhere keeps both flags in the stream's flow_stats (they are not in the patch)
    await user.clear(screen.getByLabelText('PG ID'))
    await user.type(screen.getByLabelText('PG ID'), '4')
    await user.click(save())
    expect(onSave).toHaveBeenCalledWith({ flow_stats: { enabled: true, rule_type: 'latency', stream_id: 4, vxlan: false, ieee_1588: true } })
  })

  it('a tpg rule is shown read-only', () => {
    show({ ...stored, flow_stats: { enabled: true, rule_type: 'tpg', stream_id: 1 } }, { names: [null] })
    expect(screen.getByText('TPG (edited in a later version)')).toBeInTheDocument()
    expect(screen.getByLabelText('RX stats')).toBeDisabled()
  })

  it('Previous and Next move between streams', async () => {
    const { onPrev, onNext, user } = show()
    await user.click(screen.getByRole('button', { name: 'Previous stream' }))
    await user.click(screen.getByRole('button', { name: 'Next stream' }))
    expect(onPrev).toHaveBeenCalledTimes(1)
    expect(onNext).toHaveBeenCalledTimes(1)
    cleanup()
    show(stored, { onPrev: undefined, onNext: undefined })
    expect(screen.getByRole('button', { name: 'Previous stream' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Next stream' })).toBeDisabled()
  })

  it('Cancel restores the stored values', async () => {
    const { user } = show()
    await user.clear(screen.getByLabelText('ISG (µs)'))
    await user.type(screen.getByLabelText('ISG (µs)'), '25')
    expect(save()).toBeEnabled()
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.getByLabelText('ISG (µs)')).toHaveValue('0')
    expect(save()).toBeDisabled()
  })

  it('a locked editor disables every input and Save', () => {
    show(stored, { disabled: true })
    expect(screen.getByLabelText('ISG (µs)')).toBeDisabled()
    expect(screen.getByLabelText('Enabled')).toBeDisabled()
    expect(save()).toBeDisabled()
  })

  it('a new stored stream replaces the form, and the same stream keeps edits', async () => {
    const { user, view, onSave } = show()
    await user.type(screen.getByLabelText('ISG (µs)'), '5')
    view.rerender(<StreamProperties stream={{ ...stored }} index={1} names={[null]} onSave={onSave} onPrev={vi.fn()} onNext={vi.fn()} />)
    expect(screen.getByLabelText('ISG (µs)')).toHaveValue('05')      // an equal stream from a page read keeps the edit
    view.rerender(<StreamProperties stream={{ ...stored, isg: 9 }} index={1} names={[null]} onSave={onSave} onPrev={vi.fn()} onNext={vi.fn()} />)
    expect(screen.getByLabelText('ISG (µs)')).toHaveValue('9')
  })

  it('the cache switch sets and clears vm.cache', async () => {
    const { onSave, user } = show()
    await user.selectOptions(screen.getByLabelText('Cache'), 'N packets')
    expect(screen.getByLabelText('Cache packets')).toHaveValue('5000')
    await user.click(save())
    expect(onSave).toHaveBeenCalledWith({ vm: { instructions: [], cache: 5000 } })
  })

  it('a stored next equal to the stream itself is shown and accepted unchanged', async () => {
    const loop: StreamJson = { ...burst, name: 1, next: 1 }
    const { onSave, user } = show(loop, { names: [1] })
    expect((screen.getByLabelText('Next') as HTMLSelectElement).value).toBe('1')
    expect(screen.getByRole('option', { name: '1' })).toBeInTheDocument()
    await user.clear(screen.getByLabelText('ISG (µs)'))
    await user.type(screen.getByLabelText('ISG (µs)'), '7')
    expect(save()).toBeEnabled()
    await user.click(save())
    expect(onSave).toHaveBeenCalledWith({ isg: 7 })
  })

  it('the rate is one typed box with a reading', async () => {
    const { user } = show()
    expect(screen.queryByLabelText('Rate type')).not.toBeInTheDocument()
    const rate = screen.getByLabelText('Rate')
    await user.clear(rate)
    await user.type(rate, '10 Gbps L1')
    expect(screen.getByText('= 10 Gbit/s (L1)')).toBeInTheDocument()
    expect(save()).toBeEnabled()
    await user.clear(rate)
    await user.type(rate, 'fast')
    expect(save()).toBeDisabled()
    expect(rate).toBeInvalid()
    expect(screen.getByText(/Can't read "fast"/)).toBeInTheDocument()
    expect(screen.queryByText(/^= /)).not.toBeInTheDocument()
  })

  it('there is no Name field', () => {
    show()
    expect(screen.queryByLabelText('Name')).not.toBeInTheDocument()
  })

  it('numbers may be written .5 and 1.', async () => {
    const { user } = show(burst)
    for (const label of ['IBG (µs)', 'ISG (µs)', 'Rate']) {
      await user.clear(screen.getByLabelText(label))
      await user.type(screen.getByLabelText(label), '.5')
    }
    expect(screen.queryByText(/Enter a number/)).not.toBeInTheDocument()
    await user.clear(screen.getByLabelText('ISG (µs)'))
    await user.type(screen.getByLabelText('ISG (µs)'), '1.')
    expect(save()).toBeEnabled()
  })

  it('after a Save lands, Save stays disabled until the stored stream changes', async () => {
    let finish: (ok: boolean) => void = () => undefined
    const onSave = vi.fn(() => new Promise<boolean>((r) => { finish = r }))
    const { user, view } = show(stored, { onSave })
    await user.type(screen.getByLabelText('ISG (µs)'), '5')
    await user.click(save())
    expect(save()).toBeDisabled()                  // in flight
    await act(async () => { finish(true) })
    expect(save()).toBeDisabled()                  // landed, the page read has not
    expect(onSave).toHaveBeenCalledTimes(1)
    await user.type(screen.getByLabelText('ISG (µs)'), '1')      // a further edit can be saved
    expect(save()).toBeEnabled()
    await user.keyboard('{Backspace}')
    expect(save()).toBeDisabled()
    view.rerender(<StreamProperties stream={{ ...stored, isg: 5 }} index={1} names={[null]} onSave={onSave} onPrev={vi.fn()} onNext={vi.fn()} />)
    expect(save()).toBeDisabled()                  // the form now equals the stored stream
    expect(screen.getByLabelText('ISG (µs)')).toHaveValue('5')
  })

  it('a failed Save leaves Save available', async () => {
    const onSave = vi.fn(() => Promise.resolve(false))
    const { user } = show(stored, { onSave })
    await user.type(screen.getByLabelText('ISG (µs)'), '5')
    await user.click(save())
    await vi.waitFor(() => expect(save()).toBeEnabled())
  })

  it('a stored change to one field keeps the unsaved edits to the others', async () => {
    const { user, view, onSave } = show()
    await user.type(screen.getByLabelText('ISG (µs)'), '5')
    view.rerender(<StreamProperties stream={{ ...stored, enabled: false }} index={1} names={[null]} onSave={onSave} onPrev={vi.fn()} onNext={vi.fn()} />)
    expect(screen.getByLabelText('Enabled')).not.toBeChecked()       // the changed stored value shows
    expect(screen.getByLabelText('ISG (µs)')).toHaveValue('05')      // the edit stays
  })

  it('moving with unsaved edits asks first; a clean form moves at once', async () => {
    const { user, onNext, onPrev } = show()
    await user.type(screen.getByLabelText('ISG (µs)'), '5')
    await user.click(screen.getByRole('button', { name: 'Next stream' }))
    expect(screen.getByText(/Discard changes\?/)).toBeInTheDocument()
    expect(onNext).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Keep editing' }))
    expect(screen.queryByText(/Discard changes\?/)).not.toBeInTheDocument()
    expect(screen.getByLabelText('ISG (µs)')).toHaveValue('05')
    await user.click(screen.getByRole('button', { name: 'Previous stream' }))
    await user.click(screen.getByRole('button', { name: 'Discard' }))
    expect(onPrev).toHaveBeenCalledTimes(1)
    expect(onNext).not.toHaveBeenCalled()
  })

  describe('unsaved edits are marked (RC1-31)', () => {
    it('an edited Rate and Mode are marked and counted beside Save; Cancel clears them', async () => {
      const { user, view } = show()
      expect(view.container.querySelectorAll('.edited')).toHaveLength(0)
      const rate = screen.getByLabelText('Rate')
      await user.clear(rate)
      await user.type(rate, '2 kpps')
      await user.selectOptions(screen.getByLabelText('Mode'), 'Single burst')
      expect(rate).toHaveClass('edited')
      expect(screen.getByLabelText('Mode')).toHaveClass('edited')
      expect(screen.getByLabelText('Packets')).not.toHaveClass('edited')   // shown by the mode, still its stored value
      expect(screen.getAllByText('edited')).toHaveLength(2)
      expect(screen.getByText('2 unsaved changes')).toBeInTheDocument()
      expect(rate).toHaveAccessibleDescription('edited')
      await user.click(screen.getByRole('button', { name: 'Cancel' }))
      expect(view.container.querySelectorAll('.edited')).toHaveLength(0)
      expect(screen.queryByText('edited')).not.toBeInTheDocument()
      expect(screen.queryByText(/unsaved change/)).not.toBeInTheDocument()
    })

    it('a Rate typed back to its stored text is not edited', async () => {
      const { user } = show()
      const rate = screen.getByLabelText('Rate')
      const was = (rate as HTMLInputElement).value
      await user.clear(rate)
      await user.type(rate, was)
      expect(rate).not.toHaveClass('edited')
      expect(screen.queryByText('edited')).not.toBeInTheDocument()
    })

    it('a field re-typed to an equal value is not edited: 1pps over 1 pps, ISG 00 over 0 (R6)', async () => {
      const { user, view } = show()
      expect(screen.getByLabelText('Rate')).toHaveValue('1 pps')
      await user.clear(screen.getByLabelText('Rate'))
      await user.type(screen.getByLabelText('Rate'), '1pps')
      await user.clear(screen.getByLabelText('ISG (µs)'))
      await user.type(screen.getByLabelText('ISG (µs)'), '00')
      expect(view.container.querySelectorAll('.edited')).toHaveLength(0)
      expect(screen.queryByText('edited')).not.toBeInTheDocument()
      expect(screen.queryByText(/unsaved change/)).not.toBeInTheDocument()
      expect(save()).toBeDisabled()
    })

    it('an invalid field that differs from the stored text is marked, though Save sends nothing (R6)', async () => {
      const { user } = show()
      const rate = screen.getByLabelText('Rate')
      await user.clear(rate)
      await user.type(rate, 'fast')
      expect(rate).toHaveClass('edited')
      expect(screen.getByText('1 unsaved change')).toBeInTheDocument()
    })

    it('an edited field with an error names both, the error first', async () => {
      const { user } = show(burst)
      const field = screen.getByLabelText('Bursts')
      await user.clear(field)
      await user.type(field, '0')
      const ids = (field.getAttribute('aria-describedby') ?? '').split(' ')
      expect(ids).toHaveLength(2)
      expect(document.getElementById(ids[0])).toHaveTextContent('Enter a whole number, at least 1.')
      expect(document.getElementById(ids[1])).toHaveTextContent('edited')
    })

    it('a ticked check box is marked too', async () => {
      const { user } = show()
      await user.click(screen.getByLabelText('Start paused'))
      expect(screen.getByLabelText('Start paused')).toHaveClass('edited')
      expect(screen.getByText('1 unsaved change')).toBeInTheDocument()
    })
  })
})
