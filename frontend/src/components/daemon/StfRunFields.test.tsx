import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import { NEW_STF_FORM, type PushedConfig, type ServerPorts } from './daemonForm'
import { resetHostCache } from '../../useHost'
import { StfRunFields } from './StfRunFields'

const profile = (over: Partial<api.StfProfileInfo> = {}) => (path: string) => Promise.resolve({ path, duration: 20, templates: ['cap2/Oracle.pcap', 'cap2/smtp.pcap', 'cap2/dns.pcap'], errors: [], warnings: [], ...over })
const onValid = vi.fn()

// The Run tab's job: it owns the form.
const UPLOAD = { daemon: '10.0.0.5:8090', filesPath: '/tmp/trex_files' }
const onModal = vi.fn()
function Harness({ cfgPath = '/etc/trex_cfg.yaml', pushed = null, serverPorts = { sync_port: 4701, async_port: 4600 }, upload = UPLOAD }: { cfgPath?: string; pushed?: PushedConfig | null; serverPorts?: ServerPorts; upload?: typeof UPLOAD | null }) {
  const [form, setForm] = useState(NEW_STF_FORM)
  return <StfRunFields form={form} onChange={(p) => setForm((f) => ({ ...f, ...p }))} cfgPath={cfgPath} pushed={pushed} serverPorts={serverPorts} onValid={onValid} upload={upload} onModal={onModal} />
}
const pick = async (path = 'cap2/sfr2.yaml') => userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Profile' }), path)

beforeEach(() => {
  vi.restoreAllMocks(); resetHostCache(); onValid.mockClear(); onModal.mockClear()
  vi.spyOn(api, 'daemonStfProfiles').mockResolvedValue({ source: 'stock', files: ['cap2/dns.yaml', 'cap2/sfr2.yaml', 'avl/http.yaml'] })
  vi.spyOn(api, 'daemonStfProfile').mockImplementation(profile())
})

describe('StfRunFields', () => {
  it('profiles list and the picked profile shows its templates', async () => {
    vi.spyOn(api, 'daemonStfProfile').mockImplementation(profile({ warnings: ['cps is high'] }))
    render(<Harness />)
    expect(screen.getByRole('combobox', { name: 'Source' })).toHaveDisplayValue('Stock')
    expect(api.daemonStfProfiles).toHaveBeenCalledWith('stock')
    const select = await screen.findByRole('combobox', { name: 'Profile' })
    await vi.waitFor(() => expect(within(select).getAllByRole('option')).toHaveLength(4))   // the prompt and three profiles
    await pick()
    expect(api.daemonStfProfile).toHaveBeenCalledWith('cap2/sfr2.yaml')
    expect(await screen.findByText('Templates: cap2/Oracle.pcap, cap2/smtp.pcap, cap2/dns.pcap')).toBeInTheDocument()
    expect(screen.getByText('YAML duration: 20 s')).toBeInTheDocument()
    expect(screen.getByRole('list', { name: 'Profile warnings' })).toHaveTextContent('cps is high')
    await vi.waitFor(() => expect(onValid).toHaveBeenLastCalledWith(true))
  })

  it('duration prefills from the YAML and clamps', async () => {
    for (const [yaml, want] of [[12.6, '13'], [5000, '3600'], [2, '10'], [30, '30']] as const) {
      vi.spyOn(api, 'daemonStfProfile').mockImplementation(profile({ duration: yaml }))
      const { unmount } = render(<Harness />)
      await pick()
      await vi.waitFor(() => expect(screen.getByLabelText('Duration -d (s)')).toHaveValue(Number(want)))
      unmount()
    }
    // a profile with no duration leaves the field for the user
    vi.spyOn(api, 'daemonStfProfile').mockImplementation(profile({ duration: null }))
    render(<Harness />)
    await pick()
    await screen.findByText('YAML duration: not set')
    expect(screen.getByLabelText('Duration -d (s)')).toHaveValue(null)
  })

  it('out of bounds fields show inline errors and disable Start', async () => {
    render(<Harness />)
    await pick()
    await vi.waitFor(() => expect(onValid).toHaveBeenLastCalledWith(true))
    const cases: [string, string, RegExp][] = [
      ['Duration -d (s)', '5', /Duration must be .* 10–3600/],
      ['Multiplier -m', '0', /Multiplier must be above 0 and at most 1000/],
      ['Multiplier -m', '1001', /Multiplier must be above 0 and at most 1000/],
      ['Latency -l (pps, 0 = off)', '100001', /Latency must be .* 0–100000/],
      ['Rx-check --rx-check (0 = off)', '65536', /Rx-check must be .* 0–65535/],
      ['Monitor port', '0', /Monitor port must be 1–65535/],
    ]
    for (const [label, bad, msg] of cases) {
      const input = screen.getByLabelText(label)
      const good = (input as HTMLInputElement).value
      fireEvent.change(input, { target: { value: bad } })
      expect(screen.getByText(msg)).toBeInTheDocument()
      expect(input).toBeInvalid()
      await vi.waitFor(() => expect(onValid).toHaveBeenLastCalledWith(false))
      fireEvent.change(input, { target: { value: good } })
      expect(screen.queryByText(msg)).toBeNull()
      await vi.waitFor(() => expect(onValid).toHaveBeenLastCalledWith(true))
    }
    expect(screen.getByLabelText('Duration -d (s)')).toHaveAttribute('min', '10')
    expect(screen.getByLabelText('Duration -d (s)')).toHaveAttribute('max', '3600')
  })

  it('a profile with errors disables Start', async () => {
    vi.spyOn(api, 'daemonStfProfile').mockImplementation(profile({ errors: ['template 0: cps must be above 0'], templates: [] }))
    render(<Harness />)
    expect(onValid).toHaveBeenLastCalledWith(false)   // nothing picked yet
    await pick()
    expect(await screen.findByRole('list', { name: 'Profile errors' })).toHaveTextContent('template 0: cps must be above 0')
    expect(onValid).toHaveBeenLastCalledWith(false)
    await new Promise((r) => setTimeout(r, 20))
    expect(onValid).not.toHaveBeenCalledWith(true)
  })

  it('monitor port follows the pushed config', async () => {
    const pushed = { path: '/tmp/trex_files/lab.yaml', values: { zmq_pub_port: 4650 } }
    const { unmount } = render(<Harness cfgPath="/tmp/trex_files/lab.yaml" pushed={pushed} />)
    expect(screen.getByLabelText('Monitor port')).toHaveValue(4650)
    unmount()
    const other = render(<Harness cfgPath="/etc/trex_cfg.yaml" pushed={pushed} />)
    expect(screen.getByLabelText('Monitor port')).toHaveValue(4600)   // serverPorts.async_port
    other.unmount()
    render(<Harness serverPorts={null} />)
    expect(screen.getByLabelText('Monitor port')).toHaveValue(4500)
    await userEvent.clear(screen.getByLabelText('Monitor port'))
    await userEvent.type(screen.getByLabelText('Monitor port'), '4777')
    expect(screen.getByLabelText('Monitor port')).toHaveValue(4777)
  })

  it('a profile that cannot be read shows the error and is not valid', async () => {
    vi.spyOn(api, 'daemonStfProfile').mockRejectedValue(new api.ApiError(409, 'invalid_profile', 'not a stock profile'))
    render(<Harness />)
    await pick()
    expect(await screen.findByRole('alert')).toHaveTextContent('not a stock profile')
    expect(onValid).not.toHaveBeenCalledWith(true)
  })

  it('the Uploaded source lists the uploaded profiles and picking one reads it', async () => {
    const list = vi.spyOn(api, 'daemonStfProfiles').mockImplementation((source) => Promise.resolve({
      source, files: source === 'stock' ? ['cap2/sfr2.yaml'] : ['/tmp/trex_files/tg-0123456789ab-p.yaml'] }))
    render(<Harness />)
    const source = screen.getByRole('combobox', { name: 'Source' })
    expect(within(source).getAllByRole('option').map((o) => o.textContent)).toEqual(['Stock', 'Uploaded'])
    await userEvent.selectOptions(source, 'uploaded')
    expect(list).toHaveBeenLastCalledWith('uploaded')
    await pick('/tmp/trex_files/tg-0123456789ab-p.yaml')
    expect(api.daemonStfProfile).toHaveBeenCalledWith('/tmp/trex_files/tg-0123456789ab-p.yaml')
    await vi.waitFor(() => expect(onValid).toHaveBeenLastCalledWith(true))
    // back to Stock: the uploaded pick is dropped
    await userEvent.selectOptions(source, 'stock')
    expect(await screen.findByRole('combobox', { name: 'Profile' })).toHaveValue('')
    await vi.waitFor(() => expect(onValid).toHaveBeenLastCalledWith(false))
  })

  it('Upload… browses for a YAML, then the picked path opens the upload dialog', async () => {
    vi.spyOn(api, 'fsList').mockResolvedValue({ path: '/home/u', parent: '/home', entries: [{ name: 'p.yaml', dir: false, size: 10 }], truncated: false })
    const preview = vi.spyOn(api, 'daemonStfUploadPreview').mockRejectedValue(new api.ApiError(409, 'invalid_profile', 'nope'))
    render(<Harness />)
    await userEvent.click(screen.getByRole('button', { name: 'Upload…' }))
    expect(await screen.findByRole('dialog', { name: 'Pick a stateful YAML' })).toBeInTheDocument()
    expect(onModal).toHaveBeenLastCalledWith(true)
    await userEvent.click(await screen.findByRole('button', { name: 'p.yaml' }))
    expect(screen.queryByRole('dialog', { name: 'Pick a stateful YAML' })).toBeNull()
    expect(await screen.findByRole('dialog', { name: 'Upload a stateful profile' })).toBeInTheDocument()
    expect(preview).toHaveBeenCalledWith('/home/u/p.yaml')
    expect(onModal).toHaveBeenLastCalledWith(true)
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await vi.waitFor(() => expect(onModal).toHaveBeenLastCalledWith(false))
  })

  it('Upload… is disabled without a connection', () => {
    render(<Harness upload={null} />)
    expect(screen.getByRole('button', { name: 'Upload…' })).toBeDisabled()
  })

  it('the Monitor-port note names the trex-web-gui host', async () => {
    vi.spyOn(api, 'getAbout').mockResolvedValue({ app_version: '1', trex_pin: { upstream_sha: 'x', patches: [] }, supported: 's', host: 'trex-host' })
    render(<Harness />)
    expect(await screen.findByText("the PUB port trex-host reaches the run's publisher on")).toBeInTheDocument()
  })

  it('the Monitor-port note falls back while the host is unknown', () => {
    vi.spyOn(api, 'getAbout').mockReturnValue(new Promise(() => {}))
    render(<Harness />)
    expect(screen.getByText("the PUB port the computer running trex-web-gui serve reaches the run's publisher on")).toBeInTheDocument()
  })
})
