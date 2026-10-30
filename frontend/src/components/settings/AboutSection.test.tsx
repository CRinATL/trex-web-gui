import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import { AboutSection } from './AboutSection'

afterEach(() => vi.restoreAllMocks())

describe('AboutSection', () => {
  it('shows version, pin, supported server and the data directory', async () => {
    vi.spyOn(api, 'getAbout').mockResolvedValue({ app_version: '0.1.0', trex_pin: { upstream_sha: '27e0153b5', patches: ['a', 'b', 'c'] }, supported: 'STL API 5.1 (TRex v3.08)', host: 'trex-host' })
    render(<AboutSection dataDir="/home/u/.local/share/trex-web-gui" />)
    expect(screen.getByRole('heading', { name: 'About trex-web-gui' })).toBeInTheDocument()
    expect(await screen.findByText('0.1.0')).toBeInTheDocument()
    expect(screen.getByText('27e0153b5 (3 vendor patches)')).toBeInTheDocument()
    expect(screen.getByText('STL API 5.1 (TRex v3.08)')).toBeInTheDocument()
    expect(screen.getByText('Data directory (on the backend host)')).toBeInTheDocument()
    expect(screen.getByText('/home/u/.local/share/trex-web-gui')).toBeInTheDocument()
  })

  it('states the login-URL and tunnel limits', () => {
    vi.spyOn(api, 'getAbout').mockResolvedValue({ app_version: '0.1.0', trex_pin: { upstream_sha: '27e0153b5', patches: [] }, supported: 'STL API 5.1 (TRex v3.08)', host: 'trex-host' })
    render(<AboutSection />)
    expect(screen.getByText(
      'This backend accepts a browser only through the login URL it printed at start; anything that can read that terminal or journal can use it. '
      + 'It listens on 127.0.0.1 only: from another machine, use an SSH tunnel whose local port equals the serving port.')).toBeInTheDocument()
  })
})
