import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import { SourceDialog } from './SourceDialog'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('SourceDialog', () => {
  it('shows the path and text', async () => {
    const get = vi.spyOn(api, 'profileSource').mockResolvedValue({ path: '/in/udp.py', text: 'class P:\n    pass\n' })
    const onClose = vi.fn()
    render(<SourceDialog slug="udp" onClose={onClose} />)
    const dlg = screen.getByRole('dialog', { name: 'Profile source' })
    expect(await within(dlg).findByText('/in/udp.py')).toBeInTheDocument()
    expect(get).toHaveBeenCalledWith('udp')
    const pre = dlg.querySelector('pre')
    expect(pre?.textContent).toBe('class P:\n    pass\n')
    expect(dlg.textContent?.indexOf('/in/udp.py')).toBeLessThan(dlg.textContent?.indexOf('class P') ?? 0)
    await userEvent.click(within(dlg).getByRole('button', { name: 'Close' }))
    expect(onClose).toHaveBeenCalled()
  })

  it('a 404 says the source is missing', async () => {
    vi.spyOn(api, 'profileSource').mockRejectedValue(new api.ApiError(404, 'source_missing', 'The source /in/udp.py no longer exists'))
    render(<SourceDialog slug="udp" onClose={vi.fn()} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('The source file is missing')
    expect(document.querySelector('pre')).toBeNull()
  })

  it('another refusal shows its message', async () => {
    vi.spyOn(api, 'profileSource').mockRejectedValue(new api.ApiError(413, 'too_large', 'The source is over 1 MiB'))
    render(<SourceDialog slug="udp" onClose={vi.fn()} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('The source is over 1 MiB')
  })
})
