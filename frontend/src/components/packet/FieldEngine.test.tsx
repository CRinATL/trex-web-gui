import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import { ApiError, type BuildResult, type Definitions, type PacketDoc } from '../../api'
import defsFixture from '../../__fixtures__/packet/definitions.json'
import { FieldEngine } from './FieldEngine'
import { useEditor } from './useEditor'

vi.mock('../../api', async (orig) => ({ ...(await orig<typeof import('../../api')>()), paramValues: vi.fn(), decompileVm: vi.fn(), vmPython: vi.fn() }))

const defs = (defsFixture as unknown as { definitions: Definitions }).definitions
const flowVar = { id: 'STLVmFlowVar', parameters: { name: 'ip_src', init_value: '0', max_value: '9', min_value: '0', step: '1', size: '4', op: 'inc', value_list: 'None' } }
const write = { id: 'STLVmWrFlowVar', parameters: { fv_name: 'ip_src', pkt_offset: 'IP.src', offset_fixup: '0', add_val: '0', is_big: 'true' } }
const doc = (over: Partial<PacketDoc> = {}): PacketDoc => ({
  fileType: 'trex-packet-editor', version: '1.0.0', metadata: { caption: '' }, fePrarameters: {}, feInstructions: [],
  packet: [{ id: 'Ether', fields: [] }, { id: 'IP', fields: [] }], ...over,
})
const ok = (feError: string | null = null): BuildResult => ({ binary: 'AAAA', data: [], field_engine: { instructions: {}, error: feError }, error: null })

type Build = (d: PacketDoc) => Promise<BuildResult>
let build: ReturnType<typeof vi.fn<Build>>
let keys: string[] = []
const lastDoc = () => build.mock.calls.at(-1)![0]

// The pane over a real editor state, as PacketEditor wires it.
function Host({ initial }: { initial: PacketDoc }) {
  const ed = useEditor(initial, { build })
  return <div onKeyDown={(e) => { keys.push(e.key) }}>
    <FieldEngine doc={ed.doc} result={ed.result} defs={defs} onApply={ed.apply} rejected={ed.rejected} error={ed.error} />
    <span data-testid="redo">{String(ed.canRedo)}</span><span data-testid="undo">{String(ed.canUndo)}</span>
  </div>
}
async function mount(initial: PacketDoc) {
  render(<Host initial={initial} />)
  await waitFor(() => expect(build).toHaveBeenCalled())
}

beforeEach(() => { keys = []; build = vi.fn<Build>(async () => ok()) })
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals() })

describe('FieldEngine', () => {
  it('add, edit and delete an instruction rebuild', async () => {
    await mount(doc())
    await userEvent.type(screen.getByRole('combobox', { name: 'Instruction to add' }), 'STLVmFlowVar')
    await userEvent.click(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(lastDoc().feInstructions.map((i) => i.id)).toEqual(['STLVmFlowVar']))
    const op = await screen.findByRole('combobox', { name: 'Operation (STLVmFlowVar 1)' })
    await userEvent.clear(op)
    await userEvent.type(op, 'dec{Enter}')
    await waitFor(() => expect(lastDoc().feInstructions[0].parameters.op).toBe('dec'))
    expect(screen.getByRole('list', { name: 'Field Engine structure' })).toHaveTextContent('STLVmFlowVar')
    await userEvent.click(screen.getByRole('button', { name: 'Delete STLVmFlowVar 1' }))
    await waitFor(() => expect(lastDoc().feInstructions).toEqual([]))
  })

  it('an unknown instruction is refused', async () => {
    await mount(doc())
    const calls = build.mock.calls.length
    await userEvent.type(screen.getByRole('combobox', { name: 'Instruction to add' }), 'Nope{Enter}')
    expect(screen.getByRole('alert')).toHaveTextContent('"Nope" is not a Field Engine instruction')
    expect(build.mock.calls.length).toBe(calls)
  })

  it('an ENUM parameter fetches its values on focus', async () => {
    vi.mocked(api.paramValues).mockResolvedValue({ map: { ip_src: 'ip_src', ip_dst: 'ip_dst' } })
    await mount(doc({ feInstructions: [flowVar, write] }))
    expect(api.paramValues).not.toHaveBeenCalled()
    const box = screen.getByRole('combobox', { name: 'Variable name (STLVmWrFlowVar 2)' })
    await userEvent.click(box)
    await waitFor(() => expect(api.paramValues).toHaveBeenCalledTimes(1))
    expect(vi.mocked(api.paramValues).mock.calls[0][1]).toBe('fv_name')
    await waitFor(() => expect(document.querySelectorAll(`datalist#${CSS.escape(box.getAttribute('list')!)} option`)).toHaveLength(2))
    // a parameter with a fixed dictionary lists it without asking
    const op = screen.getByRole('combobox', { name: 'Operation (STLVmFlowVar 1)' })
    expect(Array.from(document.querySelectorAll(`datalist#${CSS.escape(op.getAttribute('list')!)} option`)).map((o) => (o as HTMLOptionElement).value)).toEqual(['dec', 'inc', 'random'])
    expect(api.paramValues).toHaveBeenCalledTimes(1)
  })

  it('an FE error reverts the doc and shows the rejected value', async () => {
    build.mockImplementation(async (d) => ok(d.feInstructions.some((i) => i.parameters.op === 'bad') ? 'op must be inc, dec or random' : null))
    await mount(doc({ feInstructions: [flowVar] }))
    const op = screen.getByRole('combobox', { name: 'Operation (STLVmFlowVar 1)' })
    await userEvent.clear(op)
    await userEvent.type(op, 'bad{Enter}')
    const alert = await screen.findAllByRole('alert')
    expect(alert.map((a) => a.textContent).join(' ')).toContain('op must be inc, dec or random')
    // the document went back to the last FE that built, and was rebuilt
    await waitFor(() => expect(lastDoc().feInstructions[0].parameters.op).toBe('inc'))
    expect(op).toHaveValue('bad')
    expect(op).toHaveAttribute('aria-invalid', 'true')
    // typing again clears the mark
    await userEvent.clear(op)
    await userEvent.type(op, 'dec{Enter}')
    await waitFor(() => expect(lastDoc().feInstructions[0].parameters.op).toBe('dec'))
    await waitFor(() => expect(op).toHaveAttribute('aria-invalid', 'false'))
  })

  it('two quick edits where the first is bad go back to before the first, marked on it, nothing redoable', async () => {
    const waiting: (() => void)[] = []
    build.mockImplementation((d) => new Promise<BuildResult>((res) => {
      waiting.push(() => res(ok(d.feInstructions.some((i) => i.parameters.op === 'bad') ? 'op must be inc, dec or random' : null)))
    }))
    render(<Host initial={doc({ feInstructions: [flowVar] })} />)
    const release = async () => { await waitFor(() => expect(waiting.length).toBeGreaterThan(0)); waiting.shift()!() }
    await release()   // the open build
    const op = screen.getByRole('combobox', { name: 'Operation (STLVmFlowVar 1)' })
    const step = screen.getByRole('textbox', { name: 'Step (STLVmFlowVar 1)' })
    await userEvent.clear(op); await userEvent.type(op, 'bad{Enter}')
    await userEvent.clear(step); await userEvent.type(step, '2{Enter}')   // before the first build answers
    for (let i = 0; i < 5; i++) { await release().catch(() => undefined); if (waiting.length === 0 && build.mock.calls.length >= 5) break }
    await waitFor(() => expect(op).toHaveAttribute('aria-invalid', 'true'))
    await waitFor(() => expect(lastDoc().feInstructions[0].parameters).toMatchObject({ op: 'inc', step: '1' }))
    while (waiting.length > 0) waiting.shift()!()
    await waitFor(() => expect(op).toHaveValue('bad'))
    expect(step).toHaveAttribute('aria-invalid', 'false')
    expect(step).toHaveValue('1')
    expect(screen.getByTestId('redo')).toHaveTextContent('false')
    expect(screen.getByTestId('undo')).toHaveTextContent('false')
  })

  it('a request failure on an FE edit shows on the pane', async () => {
    await mount(doc({ feInstructions: [flowVar] }))
    build.mockRejectedValue(new ApiError(503, 'engine_restarted', 'the engine restarted'))
    const step = screen.getByRole('textbox', { name: 'Step (STLVmFlowVar 1)' })
    await userEvent.clear(step); await userEvent.type(step, '3{Enter}')
    expect((await screen.findAllByRole('alert')).map((a) => a.textContent).join(' ')).toContain('the packet engine restarted')
  })

  it('copy as Python says so when there is no clipboard', async () => {
    vi.stubGlobal('navigator', { ...navigator, clipboard: undefined })
    await mount(doc({ feInstructions: [flowVar] }))
    await userEvent.click(screen.getByRole('button', { name: 'Copy instructions as Python' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('https or on localhost')
    expect(api.vmPython).not.toHaveBeenCalled()
  })

  it('Ctrl+S and Ctrl+O reach the editor from the import box, Ctrl+Z stays in it', async () => {
    await mount(doc())
    await userEvent.click(screen.getByRole('button', { name: 'Import raw VM…' }))
    await userEvent.click(screen.getByRole('textbox', { name: 'VM JSON' }))
    await userEvent.keyboard('{Control>}s{/Control}{Control>}o{/Control}{Control>}z{/Control}')
    expect(keys.filter((k) => k !== 'Control')).toEqual(['s', 'o'])
  })

  it('an edit made while the FE already fails is kept', async () => {
    build.mockImplementation(async (d) => ok(d.feInstructions.some((i) => i.parameters.op === 'bad') ? 'op is bad' : null))
    await mount(doc({ feInstructions: [{ ...flowVar, parameters: { ...flowVar.parameters, op: 'bad' } }] }))
    await screen.findByText('op is bad')
    const step = screen.getByRole('textbox', { name: 'Step (STLVmFlowVar 1)' })
    await userEvent.clear(step)
    await userEvent.type(step, '2{Enter}')
    await waitFor(() => expect(lastDoc().feInstructions[0].parameters.step).toBe('2'))
    await waitFor(() => expect(build.mock.calls.length).toBeGreaterThan(1))
    expect(lastDoc().feInstructions[0].parameters.op).toBe('bad')
  })

  it('import raw VM replaces the instructions and lists warnings', async () => {
    vi.mocked(api.decompileVm).mockResolvedValue({
      instructions: [{ ...flowVar, parameters: { ...flowVar.parameters, value_list: 'None' } }, write],
      global_parameters: { cache_size: '10' }, warnings: ['instruction 3 was skipped'],
    })
    await mount(doc({ feInstructions: [{ id: 'STLVmFixIpv4', parameters: { offset: 'IP:0' } }] }))
    await userEvent.click(screen.getByRole('button', { name: 'Import raw VM…' }))
    const area = screen.getByRole('textbox', { name: 'VM JSON' })
    await userEvent.click(area)
    await userEvent.paste('{"instructions": [{"type": "vm_flow_var"}]}')
    await userEvent.click(screen.getByRole('button', { name: 'Import' }))
    expect(await screen.findByText('instruction 3 was skipped')).toBeInTheDocument()
    expect(vi.mocked(api.decompileVm).mock.calls[0][0]).toBe('AAAA')
    await waitFor(() => expect(lastDoc().feInstructions.map((i) => i.id)).toEqual(['STLVmFlowVar', 'STLVmWrFlowVar']))
    expect(lastDoc().fePrarameters).toEqual({ cache_size: '10' })
    // decompile writes value_list as the string "None": it goes back to the build as it came
    expect(lastDoc().feInstructions[0].parameters.value_list).toBe('None')
  })

  it('import raw VM refuses text that is not a VM', async () => {
    await mount(doc())
    await userEvent.click(screen.getByRole('button', { name: 'Import raw VM…' }))
    await userEvent.click(screen.getByRole('textbox', { name: 'VM JSON' }))
    await userEvent.paste('[1]')
    await userEvent.click(screen.getByRole('button', { name: 'Import' }))
    expect(within(screen.getByRole('group', { name: 'Import raw VM' })).getByRole('alert')).toHaveTextContent('"instructions" list')
    expect(api.decompileVm).not.toHaveBeenCalled()
  })

  it('copy as Python writes the code to the clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })
    vi.mocked(api.vmPython).mockResolvedValue({ code: 'vm = STLVM()\n' })
    await mount(doc({ feInstructions: [flowVar] }))
    await userEvent.click(screen.getByRole('button', { name: 'Copy instructions as Python' }))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('vm = STLVM()\n'))
    expect(vi.mocked(api.vmPython).mock.calls[0][0].feInstructions).toHaveLength(1)
    expect(await screen.findByText('Copied')).toBeInTheDocument()
  })

  it('copy as Python shows an unsafe-name refusal', async () => {
    const writeText = vi.fn()
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })
    vi.mocked(api.vmPython).mockRejectedValue(new ApiError(422, 'invalid_doc', 'instruction 0: name "a b" is not a Python name'))
    await mount(doc({ feInstructions: [flowVar] }))
    await userEvent.click(screen.getByRole('button', { name: 'Copy instructions as Python' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('instruction 0: name "a b" is not a Python name')
    expect(writeText).not.toHaveBeenCalled()
  })

  it('help text is decoded from base64', async () => {
    await mount(doc({ feInstructions: [flowVar] }))
    expect(screen.getByText(/^Flow variable instruction\. Allocates a variable on a stream context\./)).toBeInTheDocument()
  })

  it('cache_size edits the global parameter', async () => {
    await mount(doc())
    const box = screen.getByRole('textbox', { name: 'Cache size' })
    expect(box).toHaveAttribute('placeholder', '1000')
    await userEvent.type(box, '10{Enter}')
    await waitFor(() => expect(lastDoc().fePrarameters).toEqual({ cache_size: '10' }))
    await userEvent.clear(box)
    await userEvent.type(box, '{Enter}')
    await waitFor(() => expect(lastDoc().fePrarameters).toEqual({}))
  })

  it('a read-only pane has nothing to edit', async () => {
    render(<FieldEngine doc={doc({ feInstructions: [flowVar] })} result={ok()} defs={defs} onApply={() => undefined} readOnly />)
    for (const b of screen.getAllByRole('textbox')) expect(b).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Add' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Delete STLVmFlowVar 1' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Import raw VM…' }))
    expect(screen.queryByRole('group', { name: 'Import raw VM' })).toBeNull()
  })
})
