import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PayloadEditor } from './PayloadEditor'

afterEach(cleanup)

function setup(value?: Parameters<typeof PayloadEditor>[0]['value']) {
  const onSave = vi.fn(), onCancel = vi.fn()
  render(<PayloadEditor value={value} onSave={onSave} onCancel={onCancel} />)
  return { onSave, onCancel }
}
const pick = (name: string) => userEvent.selectOptions(screen.getByLabelText('Source'), name)

describe('PayloadEditor', () => {
  it('a Seed re-typed to an equal value is not edited: 09 over 9 (R6)', async () => {
    setup({ vtype: 'BYTES', generate: 'random_bytes', seed: 9, size: 40 })
    await userEvent.clear(screen.getByLabelText('Seed'))
    await userEvent.type(screen.getByLabelText('Seed'), '09')
    await userEvent.clear(screen.getByLabelText('Size'))
    await userEvent.type(screen.getByLabelText('Size'), '040')
    expect(screen.getByLabelText('Seed')).not.toHaveClass('edited')
    expect(screen.getByLabelText('Size')).not.toHaveClass('edited')
    expect(screen.queryByText(/unsaved change/)).not.toBeInTheDocument()
  })
  it('a control changed from the stored value is marked and counted beside Save (RC1-31)', async () => {
    setup({ vtype: 'BYTES', generate: 'random_bytes', seed: 9, size: 40 })
    expect(screen.queryByText('edited')).not.toBeInTheDocument()
    await userEvent.clear(screen.getByLabelText('Seed'))
    await userEvent.type(screen.getByLabelText('Seed'), '10')
    expect(screen.getByLabelText('Seed')).toHaveClass('edited')
    expect(screen.getByLabelText('Seed')).toHaveAccessibleDescription('edited')
    expect(screen.getByLabelText('Size')).not.toHaveClass('edited')
    expect(screen.getByText('1 unsaved change')).toBeInTheDocument()
    await userEvent.clear(screen.getByLabelText('Seed'))
    await userEvent.type(screen.getByLabelText('Seed'), '9')
    expect(screen.queryByText('edited')).not.toBeInTheDocument()
    expect(screen.queryByText(/unsaved change/)).not.toBeInTheDocument()
  })
  it('should_build_payload', async () => {
    const { onSave } = setup()
    expect(screen.getByRole('dialog', { name: 'Payload' })).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Text'), 'hello')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(onSave).toHaveBeenCalledWith({ vtype: 'BYTES', base64: 'aGVsbG8=' })
  })

  it('opens on the stored value', () => {
    setup({ vtype: 'BYTES', generate: 'random_bytes', seed: 9, size: 40 })
    expect(screen.getByLabelText('Source')).toHaveValue('random_bytes')
    expect(screen.getByLabelText('Size')).toHaveValue('40')
    expect(screen.getByLabelText('Seed')).toHaveValue('9')
  })

  it('packet size sends total_size', async () => {
    const { onSave } = setup()
    await pick('Random ASCII')
    await userEvent.selectOptions(screen.getByLabelText('Size is'), 'packet')
    await userEvent.type(screen.getByLabelText('Size'), '128')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(onSave).toHaveBeenCalledWith({ vtype: 'BYTES', generate: 'random_ascii', seed: 12345, total_size: 128 })
  })

  it('a code pattern needs no size', async () => {
    const { onSave } = setup()
    await pick('Hex bytes pattern')
    await userEvent.type(screen.getByLabelText('Hex bytes'), '0a 1b')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(onSave).toHaveBeenCalledWith({ vtype: 'BYTES', generate: 'template_code', template_code: '0a 1b' })
  })

  it('a refused value stays in the dialog with the reason', async () => {
    const { onSave } = setup()
    await pick('Text pattern')
    await userEvent.type(screen.getByLabelText('Text'), 'ab')
    await userEvent.type(screen.getByLabelText('Size'), '1048577')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(onSave).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('1048576')
  })

  it('a file is read into the value', async () => {
    const { onSave } = setup()
    await pick('File')
    await userEvent.upload(screen.getByLabelText('File'), new File([new Uint8Array([0, 1, 2, 255, 128])], 'x.bin'))
    await waitFor(() => expect(screen.getByText(/x\.bin: 5 bytes/)).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(onSave).toHaveBeenCalledWith({ vtype: 'BYTES', base64: 'AAEC/4A=' })
  })

  it('a file over 1 MiB is refused before reading', async () => {
    const { onSave } = setup()
    await pick('File pattern')
    const big = new File(['x'], 'big.bin')
    Object.defineProperty(big, 'size', { value: 1048577 })
    const read = vi.spyOn(big, 'arrayBuffer')
    fireEvent.change(screen.getByLabelText('File'), { target: { files: [big] } })
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('big.bin'))
    expect(read).not.toHaveBeenCalled()
    expect(screen.getByText('no file chosen')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Size'), '10')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(onSave).not.toHaveBeenCalled()
  })

  it('Cancel and Escape leave without saving', async () => {
    const { onSave, onCancel } = setup()
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await userEvent.type(screen.getByLabelText('Text'), 'a{Escape}')
    expect(onCancel).toHaveBeenCalledTimes(2)
    expect(onSave).not.toHaveBeenCalled()
  })

  it('keys inside the dialog do not reach the editor around it, and Ctrl+S is not the browser\'s', () => {
    const around = vi.fn()
    render(<div onKeyDown={around}><PayloadEditor value={undefined} onSave={vi.fn()} onCancel={vi.fn()} /></div>)
    const box = screen.getByLabelText('Text')
    fireEvent.keyDown(box, { key: 'z', ctrlKey: true })
    expect(around).not.toHaveBeenCalled()
    expect(fireEvent.keyDown(box, { key: 's', ctrlKey: true })).toBe(false)   // default prevented
    expect(fireEvent.keyDown(box, { key: 'a' })).toBe(true)
  })

  it('a right-click inside the dialog is the browser\'s, not the parent\'s', () => {
    const around = vi.fn()
    render(<div onContextMenu={around}><PayloadEditor value={undefined} onSave={vi.fn()} onCancel={vi.fn()} /></div>)
    expect(fireEvent.contextMenu(screen.getByLabelText('Text'))).toBe(true)
    expect(around).not.toHaveBeenCalled()
  })

  it('a CRLF payload survives an edit byte for byte', async () => {
    const { onSave } = setup({ vtype: 'BYTES', base64: btoa('GET / HTTP/1.1\r\n\r\n') })
    expect(screen.getByLabelText('Source')).toHaveValue('file')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(onSave).toHaveBeenCalledWith({ vtype: 'BYTES', base64: btoa('GET / HTTP/1.1\r\n\r\n') })
  })

  it('seed 0 is refused', async () => {
    const { onSave } = setup({ vtype: 'BYTES', generate: 'random_ascii', seed: 0, size: 8 })
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(onSave).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('seed 0')
  })

  it('an unknown generator opens as text with a note', async () => {
    const { onSave } = setup({ vtype: 'BYTES', generate: 'bogus' as never, size: 8 })
    expect(screen.getByLabelText('Source')).toHaveValue('text')
    expect(screen.getByRole('note')).toHaveTextContent('"bogus"')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(onSave).toHaveBeenCalledWith({ vtype: 'BYTES', base64: '' })
  })
})
