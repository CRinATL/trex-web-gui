import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../../api'
import * as state from '../../state'
import metadata from '../../__fixtures__/daemonMetadata.json'
import idleMsg from '../../__fixtures__/daemonMsg.json'
import { ConfigTree } from './ConfigTree'
import { defaultsOf, type MetaField, type Values } from './configModel'
import { forgetPushed } from './daemonForm'
import { DaemonDialog } from './DaemonDialog'

const META = metadata as unknown as MetaField[]
const idle = idleMsg as unknown as api.DaemonMsg
const DEVICES = [{ id: '00:03.0', label: '00:03.0 ens3 · Virtio network device' }, { id: 'dummy', label: 'dummy' }]
const SERVER_YAML = '- port_limit: 2\n  version: 2\n  interfaces: [veth0, veth1]\n'
const REPLY = { metadata: META, metadata_version: 'v1', config_yaml: SERVER_YAML, files_path: '/tmp/trex_files', devices: [{ Slot_str: '00:03.0', Interface: 'ens3', Device_str: 'Virtio network device' }, { id: 'dummy' }] }
const IP = /^(\d{1,3}\.){3}\d{1,3}$/

// A stand-in for the backend's preview: JSON as the "YAML", an IP check on the gateways, a line error for text containing "bad".
function fakePreview(b: { values: Values } | { text: string }): Promise<api.ConfigPreview> {
  if ('text' in b) {
    if (b.text.includes('bad')) return Promise.resolve({ yaml: b.text, values: null, errors: [{ path: '', line: 3, message: 'mapping values are not allowed here' }] })
    return Promise.resolve({ yaml: b.text, values: { version: 2, port_limit: 2 }, errors: [] })
  }
  const ports = (b.values.port_info ?? []) as { default_gw?: string }[]
  const errors = ports.flatMap((p, i) => (p.default_gw !== undefined && !IP.test(p.default_gw) ? [{ path: `port_info.${i}.default_gw`, line: null, message: 'must be an IP address' }] : []))
  return Promise.resolve({ yaml: JSON.stringify(b.values), values: b.values, errors })
}

describe('ConfigTree', () => {
  const noop = () => undefined
  const tree = (values: Values, errors: api.ConfigError[] = []) => render(<ConfigTree fields={META} values={values} errors={errors} devices={DEVICES} onChange={noop} />)

  it('c shows no hint: Load default leaves it to TRex, which uses 1, not the metadata\'s 4', () => {
    tree(defaultsOf(META))
    expect(screen.getByLabelText('Threads (cores) count')).not.toHaveAttribute('placeholder')
    expect(screen.getByLabelText('ZMQ publisher port')).toHaveAttribute('placeholder', '4500')
  })

  it('a control per type', () => {
    tree({ ...defaultsOf(META), port_limit: 2, interfaces: ['veth0'], port_info: [{ default_gw: '2.2.2.2' }] })
    expect(screen.getByLabelText('Port limit')).toHaveAttribute('type', 'number')                  // NUMBER
    expect(screen.getByLabelText('Bucket time (usec)')).toHaveAttribute('step', 'any')             // FLOAT
    expect(screen.getByLabelText('Low end')).toHaveAttribute('type', 'checkbox')                   // BOOLEAN
    expect(screen.getByLabelText('TRex instance prefix')).toHaveAttribute('type', 'text')          // STRING
    expect(screen.getByLabelText('Default gateway')).toHaveValue('2.2.2.2')                        // IP
    expect(screen.getByLabelText('Destination MAC')).toHaveAttribute('type', 'text')               // MAC
    expect(screen.getByText('Memory').tagName).toBe('SUMMARY')                                     // OBJECT is a fold
    const interfaces = screen.getByRole('group', { name: 'Interfaces' })                          // LIST has Add and Remove
    expect(within(interfaces).getByRole('button', { name: 'Add interface' })).toBeInTheDocument()
    expect(within(interfaces).getByRole('button', { name: 'Remove Interface 1' })).toBeInTheDocument()
  })

  it('Add and Remove edit the list', async () => {
    const onChange = vi.fn()
    render(<ConfigTree fields={META} values={{ interfaces: ['veth0', 'veth1'] }} errors={[]} devices={DEVICES} onChange={onChange} />)
    await userEvent.click(screen.getByRole('button', { name: 'Remove Interface 1' }))
    expect(onChange).toHaveBeenLastCalledWith({ interfaces: ['veth1'] })
    await userEvent.click(screen.getByRole('button', { name: 'Add interface' }))
    expect(onChange).toHaveBeenLastCalledWith({ interfaces: ['veth0', 'veth1', ''] })
  })

  it('advanced fields are folded', () => {
    tree(defaultsOf(META))
    const fold = screen.getByText('Advanced').closest('details')!
    expect(fold.open).toBe(false)
    for (const label of ['Memory', 'Timer wheel', 'TRex instance prefix', 'Limit memory', 'Threads (cores) count', 'Port bandwidth (Gbs)', 'Enable ZMQ publisher']) {
      expect(fold).toContainElement(screen.getByText(label, { selector: 'summary, label' }))
    }
    for (const label of ['Port limit', 'Version', 'Low end', 'ZMQ publisher port', 'Stateless ZMQ RPC port']) {
      expect(fold).not.toContainElement(screen.getByLabelText(label))
    }
    expect(fold).not.toContainElement(screen.getByRole('group', { name: 'Interfaces' }))
    expect(fold).not.toContainElement(screen.getByRole('group', { name: 'Ports info' }))
  })

  it('a fold holding a problem is open', () => {
    tree({ tw: { buckets: 'x' } }, [{ path: 'tw.buckets', line: null, message: 'must be an integer' }])
    expect(screen.getByText('Advanced').closest('details')!.open).toBe(true)
    expect(screen.getByText('must be an integer')).toBeInTheDocument()
  })

  it('picker offers dummy', () => {
    const { container } = tree({ interfaces: [''] })
    const input = screen.getByLabelText('Interface 1')
    const list = container.querySelector(`datalist#${input.getAttribute('list')}`)!
    expect([...list.querySelectorAll('option')].map((o) => o.getAttribute('value'))).toEqual(['00:03.0', 'dummy'])
  })
})

describe('Config and YAML tabs', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    forgetPushed()
    vi.spyOn(api, 'daemonConnect').mockResolvedValue(REPLY)
    vi.spyOn(api, 'daemonDisconnect').mockResolvedValue(idle)
    vi.spyOn(api, 'daemonConfigPreview').mockImplementation(fakePreview)
    vi.spyOn(api, 'daemonStartPreview').mockResolvedValue({ command_preview: 'x' })
    vi.spyOn(state, 'useAppState').mockImplementation(() => ({ ...state.initialState, backendOnline: true, daemonHint: { host: '127.0.0.1', port: 8090 }, daemon: idle }))
  })

  // Opens the dialog, connects (which reads the metadata) and shows the Config tab.
  async function openConfig() {
    const r = render(<DaemonDialog onClose={() => undefined} />)
    await userEvent.click(screen.getByRole('button', { name: 'Connect' }))
    await userEvent.click(screen.getByRole('tab', { name: 'Config' }))
    await screen.findByLabelText('Port limit')
    return r
  }
  const yamlTab = () => userEvent.click(screen.getByRole('tab', { name: 'YAML' }))

  it('Load default fills version 2', async () => {
    await openConfig()
    expect(screen.getByLabelText('Version')).toHaveValue(2)   // a connect starts at the defaults
    fireEvent.change(screen.getByLabelText('Version'), { target: { value: '7' } })
    expect(screen.getByLabelText('Version')).toHaveValue(7)
    await userEvent.click(screen.getByRole('button', { name: 'Load default' }))
    expect(screen.getByLabelText('Version')).toHaveValue(2)
    expect(screen.getByLabelText('ZMQ publisher port')).toHaveValue(4500)
  })

  it('Load from server reads the daemon\'s own config', async () => {
    await openConfig()
    await userEvent.click(screen.getByRole('button', { name: 'Load from server' }))
    expect(api.daemonConfigPreview).toHaveBeenCalledWith({ text: SERVER_YAML })
    await vi.waitFor(() => expect(screen.getByLabelText('Version')).toHaveValue(2))   // the stand-in's parse of the text
    expect(screen.getByLabelText('Port limit')).toHaveValue(2)
  })

  it('invalid IP shows inline and disables Push', async () => {
    await openConfig()
    await userEvent.click(screen.getByRole('button', { name: 'Add port parameters' }))
    await userEvent.type(screen.getByLabelText('Default gateway'), '2.2.2.x')
    expect(await within(screen.getByRole('tabpanel')).findByText('must be an IP address')).toBeInTheDocument()
    expect(screen.getByLabelText('Default gateway')).toHaveAttribute('aria-invalid', 'true')
    await yamlTab()
    expect(screen.getByRole('button', { name: 'Push…' })).toBeDisabled()
    expect(screen.getByRole('list', { name: 'Config problems' })).toHaveTextContent('port_info.0.default_gw: must be an IP address')
    await userEvent.click(screen.getByRole('tab', { name: 'Config' }))
    fireEvent.change(screen.getByLabelText('Default gateway'), { target: { value: '2.2.2.2' } })
    await vi.waitFor(() => expect(within(screen.getByRole('tabpanel')).queryByText('must be an IP address')).toBeNull())
    await yamlTab()
    await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Push…' })).toBeEnabled())
  })

  it('an object fold stays open when its error clears, with the focused input still there', async () => {
    // the stand-in reports the backend's "set default_gw or dest_mac" until a gateway is typed
    vi.mocked(api.daemonConfigPreview).mockImplementation((b) => {
      if ('text' in b) return fakePreview(b)
      const ports = (b.values.port_info ?? []) as { default_gw?: string }[]
      const errors = ports.flatMap((p, i) => (p.default_gw === undefined ? [{ path: `port_info.${i}.default_gw`, line: null, message: 'set default_gw or dest_mac' }] : []))
      return Promise.resolve({ yaml: JSON.stringify(b.values), values: b.values, errors })
    })
    await openConfig()
    await userEvent.click(screen.getByRole('button', { name: 'Add port parameters' }))
    const fold = screen.getByText('Port parameters 1').closest('details')!
    await vi.waitFor(() => expect(within(fold).getByText('set default_gw or dest_mac')).toBeInTheDocument())
    expect(fold.open).toBe(true)
    const gw = screen.getByLabelText('Default gateway')
    await userEvent.type(gw, '2.2.2.2')
    await vi.waitFor(() => expect(screen.queryByText('set default_gw or dest_mac')).toBeNull())
    expect(fold.open).toBe(true)
    expect(gw).toBeInTheDocument()
    expect(gw).toHaveFocus()
  })

  it('Edit YAML shows line errors and blocks switching back', async () => {
    await openConfig()
    await yamlTab()
    const toggle = screen.getByRole('checkbox', { name: 'Edit YAML' })
    await vi.waitFor(() => expect(toggle).toBeEnabled())
    await userEvent.click(toggle)
    const box = screen.getByRole('textbox', { name: 'YAML text' })
    expect((box as HTMLTextAreaElement).value).toContain('"version":2')   // starts from the form's rendering
    fireEvent.change(box, { target: { value: '- bad: [' } })
    expect(await screen.findByText('line 3: mapping values are not allowed here')).toBeInTheDocument()
    expect(api.daemonConfigPreview).toHaveBeenLastCalledWith({ text: '- bad: [' })
    expect(toggle).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Push…' })).toBeDisabled()
    fireEvent.change(box, { target: { value: '- version: 2\n' } })
    await vi.waitFor(() => expect(toggle).toBeEnabled())
    await userEvent.click(toggle)
    expect(screen.queryByRole('textbox', { name: 'YAML text' })).toBeNull()
  })

  it('Push asks first and sends confirm', async () => {
    const push = vi.spyOn(api, 'daemonConfigPush').mockResolvedValue({ path: '/tmp/trex_files/lab.yaml' })
    await openConfig()
    await yamlTab()
    fireEvent.change(screen.getByLabelText('Filename'), { target: { value: 'lab.yaml' } })
    const pushBtn = screen.getByRole('button', { name: 'Push…' })
    await vi.waitFor(() => expect(pushBtn).toBeEnabled())
    await userEvent.click(pushBtn)
    const confirm = screen.getByRole('dialog', { name: 'Push config' })
    expect(confirm).toHaveTextContent('Write lab.yaml to /tmp/trex_files on 127.0.0.1:8090?')
    expect(within(confirm).getByText(/"version":2/).tagName).toBe('CODE')
    expect(push).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('Version'), { target: { value: '7' } })   // behind the confirm: neither shown nor sent
    await userEvent.click(within(confirm).getByRole('button', { name: 'Push' }))
    expect(push).toHaveBeenCalledTimes(1)
    expect(push).toHaveBeenCalledWith({ yaml: expect.stringContaining('"version":2'), filename: 'lab.yaml', confirm: true })
    expect(push.mock.calls[0][0].yaml).not.toContain('"version":7')
    expect(screen.queryByRole('dialog', { name: 'Push config' })).toBeNull()
  })

  it('an empty filename asks for the backend\'s default and says so', async () => {
    const push = vi.spyOn(api, 'daemonConfigPush').mockResolvedValue({ path: '/tmp/trex_files/trex-web-gui-me.yaml' })
    await openConfig()
    await yamlTab()
    await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Push…' })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: 'Push…' }))
    expect(screen.getByRole('dialog', { name: 'Push config' })).toHaveTextContent('trex-web-gui-<user>.yaml')
    await userEvent.click(within(screen.getByRole('dialog', { name: 'Push config' })).getByRole('button', { name: 'Push' }))
    expect(push).toHaveBeenCalledWith(expect.objectContaining({ filename: null, confirm: true }))
  })

  it('a refused Push stays in the confirm with the backend\'s message', async () => {
    vi.spyOn(api, 'daemonConfigPush').mockRejectedValue(new api.ApiError(409, 'confirm_required', 'Pushing a config needs confirm.'))
    await openConfig()
    await yamlTab()
    await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Push…' })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: 'Push…' }))
    await userEvent.click(within(screen.getByRole('dialog', { name: 'Push config' })).getByRole('button', { name: 'Push' }))
    expect(await within(screen.getByRole('dialog', { name: 'Push config' })).findByRole('alert')).toHaveTextContent('Pushing a config needs confirm.')
  })
})
