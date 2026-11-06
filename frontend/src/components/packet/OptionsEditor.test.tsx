import { act, cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import type { BuildResult, Definitions, FieldValue, PacketDoc, PacketField } from '../../api'
import buildFixture from '../../__fixtures__/packet/buildIpOptions.json'
import defsFixture from '../../__fixtures__/packet/definitions.json'
import encodeFixture from '../../__fixtures__/packet/encodeOptions.json'
import { OptionsEditor } from './OptionsEditor'
import type { CombinedField } from './packetModel'

vi.mock('../../api', async (orig) => ({ ...(await orig<typeof import('../../api')>()), encodeOptions: vi.fn() }))
afterEach(() => { cleanup(); vi.clearAllMocks() })

const kinds = (defsFixture as unknown as { definitions: Definitions }).definitions.optionKinds
const built = (buildFixture.routerAlert as unknown as BuildResult).data[1].fields.find((f) => f.id === 'options') as PacketField

const field = (over: Partial<CombinedField> = {}): CombinedField => ({
  id: 'options', def: { id: 'options', name: 'Options', type: 'IP_OPTIONS' }, value: built.value, docValue: built.value as FieldValue, shown: String(built.hvalue),
  userSet: true, ignored: false, readOnly: false, range: [34, 40], bits: null, options: built.options ?? null, error: null, ...over,
})
// No recorded fixture holds a decode with a `problem` (Task 3 recorded none), so a test that needs one builds the tree by hand; the
// backend test test_malformed_options_build_as_typed_with_a_problem (tests/unit/test_packet_engine.py) pins that shape.
const withOptions = (o: Partial<api.DecodedOptions>) => ({ options: { ...(built.options as api.DecodedOptions), ...o } })
const machine = (base64: string) => ({ vtype: 'MACHINE' as const, base64 })

// A document is only an identity to the editor (it compares it by reference), so a minimal one stands in; a test of an undo passes another.
const emptyDoc = (): PacketDoc => ({ fileType: 'trex-packet', version: '1', metadata: { caption: '' }, packet: [], fePrarameters: {}, feInstructions: [] })
const DOC = emptyDoc()

interface Extra { pending?: boolean; epoch?: number; doc?: PacketDoc }
function setup(f: CombinedField = field(), extra: Extra = {}) {
  const onCommit = vi.fn()
  const view = render(<OptionsEditor field={f} kinds={kinds} pending={extra.pending ?? false} epoch={extra.epoch ?? 0} doc={extra.doc ?? DOC} onCommit={onCommit} />)
  const again = (g: CombinedField, e: Extra = {}) =>
    view.rerender(<OptionsEditor field={g} kinds={kinds} pending={e.pending ?? false} epoch={e.epoch ?? 0} doc={e.doc ?? DOC} onCommit={onCommit} />)
  return { onCommit, again }
}
const sentRows = () => vi.mocked(api.encodeOptions).mock.calls[0][1]

describe('OptionsEditor', () => {
  it('shows one row per option and the padding', () => {
    setup()
    expect(screen.getByText('NOP, Router Alert (alert=router_shall_examine_packet), EOL, padding 2 bytes')).toBeTruthy()
    expect(Array.from(document.querySelectorAll('.option-name'), (e) => e.textContent)).toEqual(['NOP', 'Router_Alert', 'EOL'])
    expect(screen.getByText('padding 2 bytes')).toBeTruthy()
    expect(screen.getByRole('combobox', { name: 'option 2 alert' })).toHaveValue('router_shall_examine_packet')
  })

  it('commits an edited field through encodeOptions', async () => {
    vi.mocked(api.encodeOptions).mockResolvedValue(encodeFixture.routerAlert)
    const { onCommit } = setup()
    const box = screen.getByRole('combobox', { name: 'option 2 alert' })
    await userEvent.clear(box)
    await userEvent.type(box, '1{Enter}')
    expect(api.encodeOptions).toHaveBeenCalledTimes(1)
    const [layer, rows, padding, length] = vi.mocked(api.encodeOptions).mock.calls[0]
    expect([layer, padding, length]).toEqual(['IP', 2, 6])
    expect(rows[1]).toMatchObject({ name: 'Router_Alert', fields: { alert: 1 }, edited: true })
    expect(rows[0].edited ?? false).toBe(false)
    await vi.waitFor(() => expect(onCommit).toHaveBeenCalledWith(machine(encodeFixture.routerAlert.binary)))
  })

  it('removes and reorders rows', async () => {
    vi.mocked(api.encodeOptions).mockResolvedValue(encodeFixture.routerAlert)
    setup()
    await userEvent.click(screen.getByRole('button', { name: 'Remove option 1' }))
    expect(sentRows().map((r) => r.name)).toEqual(['Router_Alert', 'EOL'])
    await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Move option 3 up' })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: 'Move option 3 up' }))
    expect(vi.mocked(api.encodeOptions).mock.calls[1][1].map((r) => r.name)).toEqual(['NOP', 'EOL', 'Router_Alert'])
  })

  it('adds an option from the kinds list', async () => {
    vi.mocked(api.encodeOptions).mockResolvedValue(encodeFixture.routerAlert)
    setup()
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Add option' }), 'RR')
    await userEvent.click(screen.getByRole('button', { name: 'Add' }))
    const rows = sentRows()
    expect(rows).toHaveLength(4)
    expect(rows[2]).toMatchObject({ name: 'RR', number: 7, fields: { pointer: 4, routers: [] } })
    expect('raw' in rows[2]).toBe(false)
  })

  it('adds an other option', async () => {
    vi.mocked(api.encodeOptions).mockResolvedValue(encodeFixture.routerAlert)
    setup()
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Add option' }), '*other')
    await userEvent.type(screen.getByRole('textbox', { name: 'Other option number' }), '25')
    await userEvent.type(screen.getByRole('textbox', { name: 'Other option hex' }), '0102')
    await userEvent.click(screen.getByRole('button', { name: 'Add' }))
    expect(sentRows()[2]).toMatchObject({ name: null, number: 25, fields: { value: '0102' } })
  })

  it('shows the refusal and commits nothing', async () => {
    vi.mocked(api.encodeOptions).mockRejectedValue(new api.ApiError(422, 'options_too_long', encodeFixture.tooLong.message, encodeFixture.tooLong))
    const { onCommit } = setup()
    await userEvent.click(screen.getByRole('button', { name: 'Remove option 1' }))
    expect(await screen.findByText(encodeFixture.tooLong.message)).toBeTruthy()
    expect(onCommit).not.toHaveBeenCalled()
  })

  it('commits hex as typed and shows the problem', async () => {
    const { onCommit } = setup()
    await userEvent.click(screen.getByRole('button', { name: 'Edit as hex' }))
    const box = screen.getByRole('textbox', { name: 'Options as hex' })
    expect(box).toHaveValue('01 94 04 00 00 00 00 00')
    await userEvent.clear(box)
    await userEvent.type(box, '07 09 00{Enter}')
    expect(onCommit).toHaveBeenCalledWith(machine('BwkA'))
    expect(api.encodeOptions).not.toHaveBeenCalled()
  })

  it('offers only hex while the bytes have a problem', () => {
    const problem = 'option 7 at byte 0 claims 9 bytes, 3 remain'
    setup(field({ value: machine('BwkA'), docValue: machine('BwkA'), ...withOptions({ rows: [], problem, raw: 'BwkA' }) }))
    expect(screen.getByText(problem)).toBeTruthy()
    expect(screen.getByRole('textbox', { name: 'Options as hex' })).toBeEnabled()
    expect(screen.queryByRole('combobox', { name: 'Add option' })).toBeNull()
    expect(screen.queryByRole('button', { name: /^Remove option|^Move option/ })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Edit as options' })).toBeNull()
  })

  it('refuses hex over 40 bytes', async () => {
    const { onCommit } = setup()
    await userEvent.click(screen.getByRole('button', { name: 'Edit as hex' }))
    const box = screen.getByRole('textbox', { name: 'Options as hex' })
    await userEvent.clear(box)
    await userEvent.type(box, `${'01'.repeat(41)}{Enter}`)
    expect(screen.getByText('options are 41 bytes; the header holds 40 at most')).toBeTruthy()
    expect(onCommit).not.toHaveBeenCalled()
  })

  it('refuses odd digits and a non-hex character', async () => {
    const { onCommit } = setup()
    await userEvent.click(screen.getByRole('button', { name: 'Edit as hex' }))
    const box = screen.getByRole('textbox', { name: 'Options as hex' })
    await userEvent.clear(box)
    await userEvent.type(box, '070{Enter}')
    expect(screen.getByText('hex needs an even number of digits')).toBeTruthy()
    await userEvent.clear(box)
    await userEvent.type(box, '0g{Enter}')
    expect(screen.getByText('hex has a character that is not 0-9 or a-f')).toBeTruthy()
    expect(onCommit).not.toHaveBeenCalled()
  })

  it('is off while a build is pending', () => {
    setup(field(), { pending: true })
    expect(screen.getByRole('button', { name: 'Remove option 1' })).toBeDisabled()
    expect(screen.getByRole('combobox', { name: 'option 2 alert' })).toBeDisabled()
    expect(screen.getByRole('combobox', { name: 'Add option' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Edit as hex' })).toBeDisabled()
  })

  it('is off while the tree lags the document', () => {
    setup(field({ docValue: machine('BwkA') }))
    expect(screen.getByRole('button', { name: 'Remove option 1' })).toBeDisabled()
    expect(screen.getByRole('combobox', { name: 'option 2 alert' })).toBeDisabled()
    expect(screen.getByRole('combobox', { name: 'Add option' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Edit as hex' })).toBeDisabled()
  })

  it('treats an absent document value as empty bytes', () => {
    setup(field({ value: machine(''), docValue: undefined, ...withOptions({ rows: [], padding: 0, length: 0 }) }))
    expect(screen.getByRole('combobox', { name: 'Add option' })).toBeEnabled()
  })

  it('drops an encode reply after the field changed', async () => {
    let resolve: (v: { binary: string }) => void = () => undefined
    vi.mocked(api.encodeOptions).mockReturnValue(new Promise((r) => { resolve = r }))
    const { onCommit, again } = setup()
    await userEvent.click(screen.getByRole('button', { name: 'Remove option 1' }))
    again(field({ value: machine('BwkA'), docValue: machine('BwkA') }))
    await act(async () => { resolve(encodeFixture.routerAlert) })
    expect(onCommit).not.toHaveBeenCalled()
  })

  it('drops a reply after a packet switch with the same docValue', async () => {
    let resolve: (v: { binary: string }) => void = () => undefined
    vi.mocked(api.encodeOptions).mockReturnValue(new Promise((r) => { resolve = r }))
    const none = field({ value: machine(''), docValue: undefined, ...withOptions({ rows: [], padding: 0, length: 0 }) })
    const { onCommit, again } = setup(none)
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Add option' }), 'NOP')
    await userEvent.click(screen.getByRole('button', { name: 'Add' }))
    again(none, { epoch: 1 })
    await act(async () => { resolve(encodeFixture.routerAlert) })
    expect(onCommit).not.toHaveBeenCalled()
  })

  it('drops a reply after it unmounts', async () => {
    let resolve: (v: { binary: string }) => void = () => undefined
    vi.mocked(api.encodeOptions).mockReturnValue(new Promise((r) => { resolve = r }))
    const { onCommit } = setup()
    await userEvent.click(screen.getByRole('button', { name: 'Remove option 1' }))
    cleanup()
    await act(async () => { resolve(encodeFixture.routerAlert) })
    expect(onCommit).not.toHaveBeenCalled()
  })

  it('offers hex after a failed build', async () => {
    const { onCommit } = setup(field({ error: 'could not build', shown: 'x' }))
    expect(screen.getByRole('textbox', { name: 'Options as hex' })).toHaveValue('01 94 04 00 00 00 00 00')
    expect(screen.getByRole('textbox', { name: 'Options as hex' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Remove option 1' })).toBeNull()
    await userEvent.type(screen.getByRole('textbox', { name: 'Options as hex' }), ' 00 00 00 00{Enter}')
    expect(onCommit).toHaveBeenCalledTimes(1)
  })

  it('keeps hex enabled after a failed build while the tree lags the document', () => {
    setup(field({ error: 'could not build', shown: '{"vtype":"MACHINE"}', value: machine('BwkA') }))   // the tree still holds the older bytes
    expect(screen.getByRole('textbox', { name: 'Options as hex' })).toBeEnabled()
    expect(screen.getByRole('textbox', { name: 'Options as hex' })).toHaveValue('01 94 04 00 00 00 00 00')
    expect(screen.getAllByText('01 94 04 00 00 00 00 00').length).toBeGreaterThan(0)   // the summary is the bytes, not the raw JSON
  })

  it('says none when there are no options', () => {
    setup(field({ value: machine(''), docValue: undefined, shown: '[]', ...withOptions({ rows: [], padding: 0, length: 0 }) }))
    expect(screen.getByText('none')).toBeTruthy()
  })

  it('keeps what was typed for an other option when the refusal comes back', async () => {
    vi.mocked(api.encodeOptions).mockRejectedValue(new api.ApiError(422, 'options_too_long', encodeFixture.tooLong.message, encodeFixture.tooLong))
    setup()
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Add option' }), '*other')
    await userEvent.type(screen.getByRole('textbox', { name: 'Other option number' }), '25')
    await userEvent.type(screen.getByRole('textbox', { name: 'Other option hex' }), '0102')
    await userEvent.click(screen.getByRole('button', { name: 'Add' }))
    expect(await screen.findByText(encodeFixture.tooLong.message)).toBeTruthy()
    expect(screen.getByRole('textbox', { name: 'Other option number' })).toHaveValue('25')
    expect(screen.getByRole('textbox', { name: 'Other option hex' })).toHaveValue('0102')
  })

  it('keeps the selected kind when the refusal comes back', async () => {
    vi.mocked(api.encodeOptions).mockRejectedValue(new api.ApiError(422, 'options_too_long', encodeFixture.tooLong.message, encodeFixture.tooLong))
    setup()
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Add option' }), 'RR')
    await userEvent.click(screen.getByRole('button', { name: 'Add' }))
    expect(await screen.findByText(encodeFixture.tooLong.message)).toBeTruthy()
    expect(screen.getByRole('combobox', { name: 'Add option' })).toHaveValue('RR')
  })

  it('clears the selected kind once the add is committed', async () => {
    vi.mocked(api.encodeOptions).mockResolvedValue(encodeFixture.routerAlert)
    const { onCommit } = setup()
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Add option' }), 'RR')
    await userEvent.click(screen.getByRole('button', { name: 'Add' }))
    await vi.waitFor(() => expect(onCommit).toHaveBeenCalled())
    expect(screen.getByRole('combobox', { name: 'Add option' })).toHaveValue('')
  })

  it('drops a refusal, the hex mode and the Add inputs when the epoch changes', async () => {
    vi.mocked(api.encodeOptions).mockRejectedValue(new api.ApiError(422, 'options_too_long', encodeFixture.tooLong.message, encodeFixture.tooLong))
    const { again } = setup()
    await userEvent.click(screen.getByRole('button', { name: 'Remove option 1' }))
    expect(await screen.findByText(encodeFixture.tooLong.message)).toBeTruthy()
    again(field(), { epoch: 1 })
    expect(screen.queryByText(encodeFixture.tooLong.message)).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Edit as hex' }))
    expect(screen.getByRole('textbox', { name: 'Options as hex' })).toBeTruthy()
    again(field(), { epoch: 2 })
    expect(screen.queryByRole('textbox', { name: 'Options as hex' })).toBeNull()
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Add option' }), '*other')
    await userEvent.type(screen.getByRole('textbox', { name: 'Other option number' }), '25')
    again(field(), { epoch: 3 })
    expect(screen.getByRole('combobox', { name: 'Add option' })).toHaveValue('')
  })

  it('drops a refusal when the document changes by an undo', async () => {
    vi.mocked(api.encodeOptions).mockRejectedValue(new api.ApiError(422, 'options_too_long', encodeFixture.tooLong.message, encodeFixture.tooLong))
    const { again } = setup()
    await userEvent.click(screen.getByRole('button', { name: 'Remove option 1' }))
    expect(await screen.findByText(encodeFixture.tooLong.message)).toBeTruthy()
    again(field(), { doc: emptyDoc() })
    expect(screen.queryByText(encodeFixture.tooLong.message)).toBeNull()
  })

  it('refuses an other number of 1 locally, adding or editing', async () => {
    const { onCommit } = setup(field({ ...withOptions({ rows: [{ name: null, number: 25, fields: { value: '0102' }, raw: 'GQQBAg==' }], padding: 0, length: 4 }) }))
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Add option' }), '*other')
    await userEvent.type(screen.getByRole('textbox', { name: 'Other option number' }), '1')
    await userEvent.type(screen.getByRole('textbox', { name: 'Other option hex' }), '0102')
    await userEvent.click(screen.getByRole('button', { name: 'Add' }))
    expect(screen.getByText('number: must be from 2 to 255')).toBeTruthy()
    const box = screen.getByRole('textbox', { name: 'option 1 number' })
    await userEvent.clear(box)
    await userEvent.type(box, '1{Enter}')
    expect(screen.getByText('number: must be from 2 to 255')).toBeTruthy()
    expect(api.encodeOptions).not.toHaveBeenCalled()
    expect(onCommit).not.toHaveBeenCalled()
  })

  it('disables the remove button of an EOL that only padding follows', () => {
    setup()   // NOP, Router Alert, EOL, padding 2
    expect(screen.getByRole('button', { name: 'Remove option 3' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Remove option 3' })).toHaveAttribute('title', 'End of Options before the padding: removing it pads to the same bytes')
    expect(screen.getByRole('button', { name: 'Remove option 1' })).toBeEnabled()
  })

  it('keeps remove on an EOL with no padding after it', () => {
    setup(field({ ...withOptions({ padding: 0 }) }))
    expect(screen.getByRole('button', { name: 'Remove option 3' })).toBeEnabled()
  })

  it('inserts an added row before the first EOL', async () => {
    vi.mocked(api.encodeOptions).mockResolvedValue(encodeFixture.routerAlert)
    setup()   // NOP, Router Alert, EOL, padding 2
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Add option' }), 'RR')
    await userEvent.click(screen.getByRole('button', { name: 'Add' }))
    expect(sentRows().map((r) => r.name)).toEqual(['NOP', 'Router_Alert', 'RR', 'EOL'])
  })

  it('inserts an added other row before the first EOL', async () => {
    vi.mocked(api.encodeOptions).mockResolvedValue(encodeFixture.routerAlert)
    setup()
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Add option' }), '*other')
    await userEvent.type(screen.getByRole('textbox', { name: 'Other option number' }), '25')
    await userEvent.type(screen.getByRole('textbox', { name: 'Other option hex' }), '0102')
    await userEvent.click(screen.getByRole('button', { name: 'Add' }))
    expect(sentRows().map((r) => r.name)).toEqual(['NOP', 'Router_Alert', null, 'EOL'])
  })

  it('keeps remove on an EOL whose removal would change the bytes', () => {
    // Router_Alert, EOL, padding 3 (94 04 00 00 00 00 00 00): no recorded fixture holds it, so the codec's decode shape is written by hand.
    const rows = [
      { name: 'Router_Alert', number: 148, fields: { alert: 0 }, raw: 'lAQAAA==' },
      { name: 'EOL', number: 0, fields: {} },
    ]
    setup(field({ value: machine('lAQAAAAAAAA='), docValue: machine('lAQAAAAAAAA='), ...withOptions({ rows: rows as api.OptionRow[], padding: 3, length: 5 }) }))
    expect(screen.getByRole('button', { name: 'Remove option 2' })).toBeEnabled()
  })
})
