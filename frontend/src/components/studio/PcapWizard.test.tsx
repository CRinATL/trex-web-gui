import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { PcapOptions } from '../../api'
import { PCAP_DEFAULTS } from './pcapOptions'
import { PcapWizard } from './PcapWizard'

afterEach(cleanup)

function Harness({ onChange }: { onChange: (o: PcapOptions) => void }) {
  const [value, setValue] = useState<PcapOptions>(PCAP_DEFAULTS)
  return <PcapWizard value={value} onChange={(o) => { setValue(o); onChange(o) }} />
}

describe('PcapWizard', () => {
  it('fixed gap sends ipg_usec and speedup 1', async () => {
    const onChange = vi.fn()
    const user = userEvent.setup()
    render(<Harness onChange={onChange} />)
    await user.clear(screen.getByRole('spinbutton', { name: 'Speedup' }))
    await user.type(screen.getByRole('spinbutton', { name: 'Speedup' }), '2')
    await user.click(screen.getByRole('radio', { name: 'Fixed gap' }))
    expect(screen.getByRole('spinbutton', { name: 'Speedup' })).toBeDisabled()
    await user.type(screen.getByRole('spinbutton', { name: 'Gap (µs)' }), '100')
    expect(onChange).toHaveBeenLastCalledWith({ ...PCAP_DEFAULTS, ipg_usec: 100, speedup: 1 })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('from the pcap sends ipg_usec null and the speedup', async () => {
    const onChange = vi.fn()
    const user = userEvent.setup()
    render(<Harness onChange={onChange} />)
    expect(screen.getByRole('radio', { name: /From the pcap/ })).toBeChecked()
    expect(screen.getByRole('spinbutton', { name: 'Gap (µs)' })).toBeDisabled()
    await user.clear(screen.getByRole('spinbutton', { name: 'Speedup' }))
    await user.type(screen.getByRole('spinbutton', { name: 'Speedup' }), '4')
    expect(onChange).toHaveBeenLastCalledWith({ ...PCAP_DEFAULTS, ipg_usec: null, speedup: 4 })
  })

  it('a rewrite count past 255.255.255.255 is an error', async () => {
    const onChange = vi.fn()
    const user = userEvent.setup()
    render(<Harness onChange={onChange} />)
    expect(screen.getByRole('checkbox', { name: 'Rewrite source IPv4' })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Rewrite destination IPv4' })).not.toBeChecked()
    await user.click(screen.getByRole('checkbox', { name: 'Rewrite source IPv4' }))
    await user.type(screen.getByRole('textbox', { name: 'Source start' }), '255.255.255.250')
    await user.clear(screen.getByRole('spinbutton', { name: 'Source count' }))
    await user.type(screen.getByRole('spinbutton', { name: 'Source count' }), '6')
    expect(onChange).toHaveBeenLastCalledWith({ ...PCAP_DEFAULTS, rewrite_src: { start: '255.255.255.250', count: 6 } })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()   // .250 to .255 fits
    await user.clear(screen.getByRole('spinbutton', { name: 'Source count' }))
    await user.type(screen.getByRole('spinbutton', { name: 'Source count' }), '7')
    expect(screen.getByRole('alert')).toHaveTextContent('Rewrite source: 255.255.255.250 + 7 addresses pass 255.255.255.255')
  })

  it('the rewrite start is sent trimmed', async () => {
    const onChange = vi.fn()
    const user = userEvent.setup()
    render(<Harness onChange={onChange} />)
    await user.click(screen.getByRole('checkbox', { name: 'Rewrite destination IPv4' }))
    await user.type(screen.getByRole('textbox', { name: 'Destination start' }), ' 1.2.3.4 ')
    expect(onChange).toHaveBeenLastCalledWith({ ...PCAP_DEFAULTS, rewrite_dst: { start: '1.2.3.4', count: 1 } })
    expect(screen.getByRole('textbox', { name: 'Destination start' })).toHaveValue(' 1.2.3.4 ')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('the help says any pcap is accepted and the rewrite needs IPv4', () => {
    render(<Harness onChange={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'About pcap import' })).toBeInTheDocument()
    expect(screen.getByText(/Any pcap is accepted/)).toBeInTheDocument()
    expect(screen.getByText(/rewrite needs IPv4 packets/)).toBeInTheDocument()
  })

  it('each timing option is on its own line: radio, label, input', () => {
    render(<Harness onChange={vi.fn()} />)
    const from = screen.getByRole('radio', { name: /From the pcap/ }).closest('.pcap-timing-option')!
    const gap = screen.getByRole('radio', { name: 'Fixed gap' }).closest('.pcap-timing-option')!
    expect(from).not.toBe(gap)
    expect(from).toContainElement(screen.getByRole('spinbutton', { name: 'Speedup' }))
    expect(gap).toContainElement(screen.getByRole('spinbutton', { name: 'Gap (µs)' }))
    expect(from).not.toContainElement(screen.getByRole('radio', { name: 'Fixed gap' }))
  })

  it('each rewrite count shows a visible label and keeps its accessible name', async () => {
    render(<Harness onChange={vi.fn()} />)
    await userEvent.click(screen.getByRole('checkbox', { name: 'Rewrite source IPv4' }))
    await userEvent.click(screen.getByRole('checkbox', { name: 'Rewrite destination IPv4' }))
    for (const what of ['Source', 'Destination']) {
      const count = screen.getByRole('spinbutton', { name: `${what} count` })
      expect(count.id).toBeTruthy()
      expect(document.querySelector(`label[for="${count.id}"]`)).toHaveTextContent('Count')
      expect(screen.getByRole('textbox', { name: `${what} start` })).toBeInTheDocument()
    }
  })
})
